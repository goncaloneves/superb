from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .signals import SignalOutput, atr


@dataclass
class Trade:
    symbol: str
    signal: str
    side: int                # +1 long, -1 short
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    size: float = 0.0        # contracts (denominated in underlying)
    notional_entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    fees: float = 0.0
    funding_cost: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""
    score_at_entry: float = 0.0
    bars_held: int = 0


@dataclass
class Position:
    trade: Trade
    open_bar: int


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    daily_pnl: pd.Series
    config: BacktestConfig
    per_signal_summary: pd.DataFrame = field(default_factory=pd.DataFrame)


def _hourly_funding_factor(interval: str) -> float:
    # funding_rate column is hourly. If interval != 1h, scale.
    if interval.endswith("m"):
        return int(interval[:-1]) / 60.0
    if interval.endswith("h"):
        return float(interval[:-1])
    if interval.endswith("d"):
        return float(interval[:-1]) * 24.0
    return 1.0


def _passes_gates(df: pd.DataFrame, i: int, side: int, cfg: BacktestConfig) -> bool:
    if i <= 0:
        return False
    row = df.iloc[i]
    # liquidity gate (rough: bar volume * price annualized to 24h)
    bars_per_day = 24.0 / _hourly_funding_factor(cfg.interval or "1h")
    vol_24h_usd = df["volume"].iloc[max(0, i - int(bars_per_day)):i].sum() * row["close"]
    if vol_24h_usd < cfg.min_24h_volume_usd:
        return False
    # funding gate against direction
    if side == 1 and row["funding_rate"] > cfg.funding_extreme_pct_per_hour:
        return False
    if side == -1 and row["funding_rate"] < -cfg.funding_extreme_pct_per_hour:
        return False
    return True


def _combine_signals(signal_outputs: dict[str, SignalOutput], weights: dict[str, float] | None
                     ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (combined_score, dominant_side, attributing_signal_name_series)."""
    if not signal_outputs:
        idx = pd.Index([])
        return pd.Series(dtype=float, index=idx), pd.Series(dtype=int, index=idx), pd.Series(dtype=object, index=idx)
    keys = list(signal_outputs.keys())
    idx = signal_outputs[keys[0]].score.index
    w = {k: (weights or {}).get(k, 1.0) for k in keys}
    score_mat = pd.DataFrame({k: signal_outputs[k].score * w[k] for k in keys}, index=idx)
    side_mat = pd.DataFrame({k: signal_outputs[k].side for k in keys}, index=idx)
    # net side weighted by score
    weighted_side = (score_mat * side_mat).sum(axis=1)
    dominant_side = np.sign(weighted_side).astype(int)
    # combined score uses sum of agreeing-side scores normalized
    agreeing = score_mat.where(side_mat.eq(pd.Series(dominant_side, index=idx), axis=0), 0.0)
    combined = (agreeing.sum(axis=1) / max(1.0, sum(w.values()))).clip(0, 1)
    attributing = agreeing.idxmax(axis=1)
    return combined, pd.Series(dominant_side, index=idx), attributing


def run_backtest(data: dict[str, pd.DataFrame],
                 signal_outputs_by_coin: dict[str, dict[str, SignalOutput]],
                 cfg: BacktestConfig,
                 weights: dict[str, float] | None = None,
                 per_signal_mode: bool = False) -> BacktestResult:
    """
    Two modes:
      - per_signal_mode=False: combined score drives trades; trades tagged by dominant signal
      - per_signal_mode=True: each signal runs in isolation; trades tagged with that signal
    """
    trades: list[Trade] = []
    equity = cfg.initial_equity_usd
    interval_hours = _hourly_funding_factor(cfg.interval)

    # Build a master timeline (sorted union of all symbol indices)
    all_idx: set[pd.Timestamp] = set()
    for df in data.values():
        all_idx.update(df.index.tolist())
    timeline = sorted(all_idx)
    if not timeline:
        return BacktestResult(trades=[], equity_curve=pd.Series(dtype=float),
                              daily_pnl=pd.Series(dtype=float), config=cfg)

    # Pre-compute per-coin signal aggregations
    coin_state: dict[str, dict] = {}
    for coin, df in data.items():
        sigs = signal_outputs_by_coin.get(coin, {})
        if not sigs:
            continue
        atr_series = atr(df, 14)
        if per_signal_mode:
            # one channel per signal
            channels = {name: (so.score, so.side, name) for name, so in sigs.items()}
        else:
            combined, dom_side, attrib = _combine_signals(sigs, weights)
            channels = {"combined": (combined, dom_side, attrib)}
        coin_state[coin] = {"df": df, "atr": atr_series, "channels": channels,
                            "positions": {}}  # channel -> Position

    equity_curve: list[tuple[pd.Timestamp, float]] = []
    daily_pnl_by_date: dict[pd.Timestamp, float] = {}
    open_positions_count = 0
    day_kill_active_date: pd.Timestamp | None = None

    for t in timeline:
        utc_date = t.normalize()
        # reset day kill at new day
        if day_kill_active_date is not None and utc_date != day_kill_active_date:
            day_kill_active_date = None

        # === Process every open position: check SL/TP/time-stop/signal-decay ===
        for coin, st in coin_state.items():
            df = st["df"]
            if t not in df.index:
                continue
            i = df.index.get_loc(t)
            row = df.iloc[i]
            for ch_name, pos in list(st["positions"].items()):
                tr = pos.trade
                # funding accrual: hourly_rate * interval_hours * notional, longs pay positive
                hourly_rate = row["funding_rate"]
                funding_inc = hourly_rate * interval_hours * (tr.size * row["close"]) * tr.side
                tr.funding_cost += funding_inc
                tr.bars_held += 1

                exit_price = None
                reason = ""
                # SL/TP intra-bar — conservative: if both could trigger, SL wins
                if tr.side == 1:
                    if row["low"] <= tr.sl:
                        exit_price = tr.sl
                        reason = "sl"
                    elif row["high"] >= tr.tp:
                        exit_price = tr.tp
                        reason = "tp"
                else:
                    if row["high"] >= tr.sl:
                        exit_price = tr.sl
                        reason = "sl"
                    elif row["low"] <= tr.tp:
                        exit_price = tr.tp
                        reason = "tp"

                # time stop
                if exit_price is None and tr.bars_held >= cfg.time_stop_bars:
                    exit_price = row["close"]
                    reason = "time"

                # signal decay exit
                if exit_price is None and cfg.signal_decay_exit:
                    score_series, _, _ = st["channels"][ch_name]
                    if score_series.iloc[i] < cfg.exit_threshold:
                        exit_price = row["close"]
                        reason = "signal_decay"

                if exit_price is not None:
                    # apply slippage on exit (worse fill)
                    slip = cfg.slippage_bps / 10_000.0
                    eff_exit = exit_price * (1 - slip) if tr.side == 1 else exit_price * (1 + slip)
                    exit_fee = abs(tr.size * eff_exit) * cfg.taker_fee_bps / 10_000.0
                    tr.fees += exit_fee
                    raw_pnl = (eff_exit - tr.entry_price) * tr.size * tr.side
                    tr.pnl = raw_pnl - tr.fees - tr.funding_cost
                    tr.exit_price = eff_exit
                    tr.exit_time = t
                    tr.exit_reason = reason
                    equity += tr.pnl
                    daily_pnl_by_date[utc_date] = daily_pnl_by_date.get(utc_date, 0.0) + tr.pnl
                    trades.append(tr)
                    del st["positions"][ch_name]
                    open_positions_count -= 1

        # Day-kill switch (after processing exits for the bar)
        day_pnl = daily_pnl_by_date.get(utc_date, 0.0)
        if day_kill_active_date is None and day_pnl <= -cfg.daily_dd_kill_frac * cfg.initial_equity_usd:
            day_kill_active_date = utc_date

        # === Consider new entries ===
        if day_kill_active_date != utc_date and open_positions_count < cfg.max_concurrent_positions:
            for coin, st in coin_state.items():
                df = st["df"]
                if t not in df.index:
                    continue
                i = df.index.get_loc(t)
                if i + 1 >= len(df):
                    continue  # need next bar to fill
                for ch_name, (score_s, side_s, attrib_s) in st["channels"].items():
                    if ch_name in st["positions"]:
                        continue
                    score = float(score_s.iloc[i]) if not np.isnan(score_s.iloc[i]) else 0.0
                    side = int(side_s.iloc[i]) if not np.isnan(side_s.iloc[i]) else 0
                    if score < cfg.entry_threshold or side == 0:
                        continue
                    if not cfg.enable_shorts and side == -1:
                        continue
                    if not _passes_gates(df, i, side, cfg):
                        continue

                    next_row = df.iloc[i + 1]
                    next_t = df.index[i + 1]
                    raw_entry = next_row["open"]
                    slip = cfg.slippage_bps / 10_000.0
                    entry_price = raw_entry * (1 + slip) if side == 1 else raw_entry * (1 - slip)
                    a = float(st["atr"].iloc[i]) if not np.isnan(st["atr"].iloc[i]) else 0.0
                    if a <= 0:
                        continue
                    sl_dist = cfg.sl_atr_mult * a
                    sl = entry_price - sl_dist if side == 1 else entry_price + sl_dist
                    tp = entry_price + cfg.tp_r_multiple * sl_dist if side == 1 \
                        else entry_price - cfg.tp_r_multiple * sl_dist

                    risk_usd = max(1.0, cfg.risk_per_trade * equity)
                    size = risk_usd / sl_dist
                    max_notional = cfg.max_position_notional_frac * equity
                    if size * entry_price > max_notional:
                        size = max_notional / entry_price
                    if size <= 0:
                        continue

                    entry_fee = size * entry_price * cfg.taker_fee_bps / 10_000.0

                    signal_name = ch_name if per_signal_mode else str(attrib_s.iloc[i])
                    tr = Trade(
                        symbol=coin, signal=signal_name, side=side,
                        entry_time=next_t, entry_price=entry_price, size=size,
                        notional_entry=size * entry_price, sl=sl, tp=tp,
                        fees=entry_fee, score_at_entry=score,
                    )
                    st["positions"][ch_name] = Position(trade=tr, open_bar=i + 1)
                    open_positions_count += 1
                    if open_positions_count >= cfg.max_concurrent_positions:
                        break
                if open_positions_count >= cfg.max_concurrent_positions:
                    break

        equity_curve.append((t, equity))

    # Force-close any still-open positions at last available close
    for coin, st in coin_state.items():
        df = st["df"]
        for ch_name, pos in list(st["positions"].items()):
            tr = pos.trade
            last_row = df.iloc[-1]
            slip = cfg.slippage_bps / 10_000.0
            eff_exit = last_row["close"] * (1 - slip) if tr.side == 1 else last_row["close"] * (1 + slip)
            exit_fee = abs(tr.size * eff_exit) * cfg.taker_fee_bps / 10_000.0
            tr.fees += exit_fee
            tr.pnl = (eff_exit - tr.entry_price) * tr.size * tr.side - tr.fees - tr.funding_cost
            tr.exit_price = eff_exit
            tr.exit_time = df.index[-1]
            tr.exit_reason = "forced_close"
            trades.append(tr)

    eq = pd.Series({t: v for t, v in equity_curve}).sort_index()
    daily_pnl = pd.Series(daily_pnl_by_date).sort_index()
    return BacktestResult(trades=trades, equity_curve=eq, daily_pnl=daily_pnl, config=cfg)


def run_each_signal_isolated(data: dict[str, pd.DataFrame],
                             signal_outputs_by_coin: dict[str, dict[str, SignalOutput]],
                             cfg: BacktestConfig) -> dict[str, BacktestResult]:
    """Run an isolated backtest per signal so we can compare standalone edge."""
    out: dict[str, BacktestResult] = {}
    # Collect all signal names
    names: set[str] = set()
    for sigs in signal_outputs_by_coin.values():
        names.update(sigs.keys())
    for name in sorted(names):
        sub = {coin: {name: sigs[name]} for coin, sigs in signal_outputs_by_coin.items() if name in sigs}
        res = run_backtest(data, sub, cfg, per_signal_mode=True)
        out[name] = res
    return out
