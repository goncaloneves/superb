from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import BacktestResult, Trade


@dataclass
class PerfStats:
    n_trades: int
    hit_rate: float
    avg_pnl: float
    median_pnl: float
    profit_factor: float
    total_pnl: float
    avg_r: float                # mean PnL / mean risked $
    sharpe_daily: float
    sortino_daily: float
    max_drawdown_frac: float
    cagr: float
    exposure: float             # fraction of bars with any position
    avg_hold_bars: float
    sl_exit_frac: float
    tp_exit_frac: float
    time_exit_frac: float
    decay_exit_frac: float


def _safe_div(a, b) -> float:
    return float(a) / float(b) if b not in (0, 0.0, None) and not np.isnan(b) else 0.0


def trades_to_frame(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        rows.append({
            "symbol": t.symbol, "signal": t.signal, "side": t.side,
            "entry_time": t.entry_time, "exit_time": t.exit_time,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "size": t.size, "notional": t.notional_entry,
            "sl": t.sl, "tp": t.tp,
            "fees": t.fees, "funding_cost": t.funding_cost,
            "pnl": t.pnl, "exit_reason": t.exit_reason,
            "score_at_entry": t.score_at_entry, "bars_held": t.bars_held,
            "risk_usd": abs(t.entry_price - t.sl) * t.size,
        })
    df = pd.DataFrame(rows)
    df["r_multiple"] = df["pnl"] / df["risk_usd"].replace(0, np.nan)
    return df


def perf_stats(result: BacktestResult) -> PerfStats:
    df = trades_to_frame(result.trades)
    if df.empty:
        return PerfStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    wins = df[df["pnl"] > 0]["pnl"].sum()
    losses = -df[df["pnl"] < 0]["pnl"].sum()
    pf = _safe_div(wins, losses) if losses > 0 else float("inf") if wins > 0 else 0.0

    # equity curve
    eq = result.equity_curve
    if not eq.empty:
        peak = eq.cummax()
        dd = (eq - peak) / peak
        max_dd = float(dd.min()) if len(dd) else 0.0
        days = max(1.0, (eq.index[-1] - eq.index[0]).total_seconds() / 86400.0)
        total_ret = (eq.iloc[-1] / eq.iloc[0]) - 1.0
        cagr = (1 + total_ret) ** (365.0 / days) - 1.0 if days >= 1 else 0.0
    else:
        max_dd, cagr = 0.0, 0.0

    dp = result.daily_pnl
    if not dp.empty and dp.std(ddof=0) > 0:
        sharpe = float(dp.mean() / dp.std(ddof=0) * np.sqrt(365))
        downside = dp[dp < 0]
        sortino = float(dp.mean() / downside.std(ddof=0) * np.sqrt(365)) if len(downside) > 1 else 0.0
    else:
        sharpe, sortino = 0.0, 0.0

    exit_counts = df["exit_reason"].value_counts(normalize=True).to_dict()
    n = len(df)
    return PerfStats(
        n_trades=n,
        hit_rate=float((df["pnl"] > 0).mean()),
        avg_pnl=float(df["pnl"].mean()),
        median_pnl=float(df["pnl"].median()),
        profit_factor=pf,
        total_pnl=float(df["pnl"].sum()),
        avg_r=float(df["r_multiple"].mean()) if df["r_multiple"].notna().any() else 0.0,
        sharpe_daily=sharpe,
        sortino_daily=sortino,
        max_drawdown_frac=max_dd,
        cagr=cagr,
        exposure=_safe_div(df["bars_held"].sum(), len(result.equity_curve) * max(1, df["symbol"].nunique())),
        avg_hold_bars=float(df["bars_held"].mean()),
        sl_exit_frac=float(exit_counts.get("sl", 0.0)),
        tp_exit_frac=float(exit_counts.get("tp", 0.0)),
        time_exit_frac=float(exit_counts.get("time", 0.0)),
        decay_exit_frac=float(exit_counts.get("signal_decay", 0.0)),
    )


def per_signal_summary(results: dict[str, BacktestResult]) -> pd.DataFrame:
    rows = []
    for name, res in results.items():
        s = perf_stats(res)
        rows.append({
            "signal": name,
            "n_trades": s.n_trades,
            "hit_rate": round(s.hit_rate, 3),
            "avg_pnl": round(s.avg_pnl, 2),
            "median_pnl": round(s.median_pnl, 2),
            "total_pnl": round(s.total_pnl, 2),
            "avg_r": round(s.avg_r, 3),
            "profit_factor": round(s.profit_factor, 3) if s.profit_factor != float("inf") else "inf",
            "sharpe_daily": round(s.sharpe_daily, 2),
            "sortino": round(s.sortino_daily, 2),
            "max_dd": round(s.max_drawdown_frac, 3),
            "cagr": round(s.cagr, 3),
            "avg_hold_bars": round(s.avg_hold_bars, 1),
            "tp%": round(s.tp_exit_frac, 2),
            "sl%": round(s.sl_exit_frac, 2),
            "time%": round(s.time_exit_frac, 2),
            "decay%": round(s.decay_exit_frac, 2),
        })
    out = pd.DataFrame(rows).sort_values("total_pnl", ascending=False).reset_index(drop=True)
    return out


def per_symbol_summary(result: BacktestResult) -> pd.DataFrame:
    df = trades_to_frame(result.trades)
    if df.empty:
        return df
    grouped = df.groupby("symbol").agg(
        n=("pnl", "size"),
        hit_rate=("pnl", lambda x: float((x > 0).mean())),
        total_pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"),
        avg_r=("r_multiple", "mean"),
    ).sort_values("total_pnl", ascending=False)
    return grouped.round(3)


def walk_forward_split(timeline: pd.DatetimeIndex, folds: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if len(timeline) < folds * 2:
        return [(timeline[0], timeline[-1])]
    step = len(timeline) // folds
    bounds = []
    for i in range(folds):
        a = timeline[i * step]
        b = timeline[(i + 1) * step - 1] if i < folds - 1 else timeline[-1]
        bounds.append((a, b))
    return bounds


def walk_forward_eval(data: dict[str, pd.DataFrame],
                      signal_outputs_by_coin: dict[str, dict],
                      cfg, runner, folds: int = 4) -> pd.DataFrame:
    """Apply `runner(data, sigs, cfg)` across folds; return per-fold metrics."""
    # build union timeline
    idx = pd.DatetimeIndex(sorted({t for df in data.values() for t in df.index.tolist()}))
    bounds = walk_forward_split(idx, folds)
    rows = []
    for i, (a, b) in enumerate(bounds):
        sub_data = {c: df.loc[(df.index >= a) & (df.index <= b)] for c, df in data.items()}
        sub_data = {c: d for c, d in sub_data.items() if not d.empty}
        sub_sigs: dict[str, dict] = {}
        for c, sigs in signal_outputs_by_coin.items():
            if c not in sub_data:
                continue
            mask = (sub_data[c].index)
            sub_sigs[c] = {name: type(so)(score=so.score.reindex(mask).fillna(0.0),
                                          side=so.side.reindex(mask).fillna(0).astype(int))
                           for name, so in sigs.items()}
        res = runner(sub_data, sub_sigs, cfg)
        s = perf_stats(res)
        rows.append({
            "fold": i + 1, "from": str(a.date()), "to": str(b.date()),
            "n_trades": s.n_trades, "hit_rate": round(s.hit_rate, 3),
            "total_pnl": round(s.total_pnl, 2), "sharpe": round(s.sharpe_daily, 2),
            "max_dd": round(s.max_drawdown_frac, 3), "profit_factor":
                (round(s.profit_factor, 3) if s.profit_factor != float("inf") else "inf"),
        })
    return pd.DataFrame(rows)
