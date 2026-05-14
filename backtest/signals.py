from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class SignalOutput:
    score: pd.Series      # 0..1 strength
    side: pd.Series       # +1 long, -1 short, 0 neutral


SignalFn = Callable[[pd.DataFrame], SignalOutput]


def _zclip(x: pd.Series, k: float = 3.0) -> pd.Series:
    z = (x - x.rolling(168, min_periods=24).mean()) / x.rolling(168, min_periods=24).std(ddof=0)
    return z.clip(-k, k)


def _norm01(z: pd.Series, k: float = 3.0) -> pd.Series:
    return ((z.clip(-k, k) + k) / (2 * k)).fillna(0.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=max(2, n // 2)).mean()


# ---- individual signals ----------------------------------------------------

def volume_zscore_breakout(df: pd.DataFrame, window: int = 168, threshold: float = 2.0) -> SignalOutput:
    z = (df["volume"] - df["volume"].rolling(window, min_periods=24).mean()) / \
        df["volume"].rolling(window, min_periods=24).std(ddof=0)
    ret = df["close"].pct_change()
    side = np.where(ret > 0, 1, np.where(ret < 0, -1, 0))
    raw = (z - threshold).clip(lower=0) / 3.0
    score = raw.clip(0, 1).fillna(0.0)
    return SignalOutput(score=score, side=pd.Series(side, index=df.index))


def momentum_breakout(df: pd.DataFrame, lookback: int = 20) -> SignalOutput:
    hi = df["high"].rolling(lookback).max().shift(1)
    lo = df["low"].rolling(lookback).min().shift(1)
    long_break = (df["close"] > hi).astype(float)
    short_break = (df["close"] < lo).astype(float)
    score = (long_break + short_break).clip(0, 1)
    side = pd.Series(0, index=df.index, dtype=int)
    side[long_break == 1] = 1
    side[short_break == 1] = -1
    return SignalOutput(score=score, side=side)


def momentum_returns(df: pd.DataFrame, lookback: int = 24) -> SignalOutput:
    r = df["close"].pct_change(lookback)
    z = _zclip(r)
    score = (z.abs() / 3.0).clip(0, 1).fillna(0.0)
    side = pd.Series(np.sign(z).fillna(0).astype(int), index=df.index)
    return SignalOutput(score=score, side=side)


def funding_mean_reversion(df: pd.DataFrame, threshold: float = 0.0003) -> SignalOutput:
    """Fade extreme funding. Hourly funding > +threshold => short; < -threshold => long."""
    f = df["funding_rate"].fillna(0.0)
    sustained = f.rolling(8, min_periods=4).mean()
    raw = (sustained.abs() - threshold).clip(lower=0) / (threshold * 4)
    score = raw.clip(0, 1).fillna(0.0)
    side = pd.Series(np.where(sustained > 0, -1, np.where(sustained < 0, 1, 0)), index=df.index)
    return SignalOutput(score=score, side=side)


def funding_flip(df: pd.DataFrame) -> SignalOutput:
    """Funding rate crosses zero — early-regime-change signal."""
    f = df["funding_rate"].fillna(0.0)
    prev = f.shift(1)
    cross_up = ((prev <= 0) & (f > 0)).astype(int)
    cross_dn = ((prev >= 0) & (f < 0)).astype(int)
    score = (cross_up + cross_dn).clip(0, 1).astype(float)
    side = pd.Series(np.where(cross_up == 1, 1, np.where(cross_dn == 1, -1, 0)), index=df.index)
    return SignalOutput(score=score, side=side)


def liquidation_cascade_bounce(df: pd.DataFrame, vol_z_thr: float = 3.0, body_atr_mult: float = 2.0) -> SignalOutput:
    """Counter-trend after a high-volume capitulation candle (proxy for liq cascade)."""
    a = atr(df, 14)
    body = (df["close"] - df["open"]).abs()
    vol_z = _zclip(df["volume"])
    big_red = (df["close"] < df["open"]) & (body > body_atr_mult * a) & (vol_z > vol_z_thr)
    big_green = (df["close"] > df["open"]) & (body > body_atr_mult * a) & (vol_z > vol_z_thr)
    score = pd.Series(0.0, index=df.index)
    score[big_red | big_green] = 0.8
    side = pd.Series(0, index=df.index, dtype=int)
    side[big_red] = 1     # bounce-long after long-liquidation cascade
    side[big_green] = -1  # fade after short-squeeze blow-off
    return SignalOutput(score=score, side=side)


def volatility_compression_breakout(df: pd.DataFrame, n: int = 24) -> SignalOutput:
    """Bollinger-style squeeze break: width contracts then price exits."""
    ma = df["close"].rolling(n).mean()
    sd = df["close"].rolling(n).std(ddof=0)
    width = (sd / ma).fillna(0)
    width_low = width < width.rolling(n * 4, min_periods=n).quantile(0.2)
    upper = ma + 2 * sd
    lower = ma - 2 * sd
    long_brk = width_low.shift(1).fillna(False) & (df["close"] > upper)
    short_brk = width_low.shift(1).fillna(False) & (df["close"] < lower)
    score = pd.Series(0.0, index=df.index)
    score[long_brk | short_brk] = 0.7
    side = pd.Series(0, index=df.index, dtype=int)
    side[long_brk] = 1
    side[short_brk] = -1
    return SignalOutput(score=score, side=side)


def trend_strength_adx_like(df: pd.DataFrame, n: int = 14) -> SignalOutput:
    """Simple directional movement strength."""
    up_move = df["high"].diff()
    dn_move = -df["low"].diff()
    plus_dm = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
    minus_dm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
    tr = pd.concat([(df["high"] - df["low"]).abs(),
                    (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    atr_n = tr.rolling(n).sum().replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(n).sum() / atr_n
    minus_di = 100 * minus_dm.rolling(n).sum() / atr_n
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(n).mean().fillna(0)
    score = (adx / 50.0).clip(0, 1)
    side = pd.Series(np.where(plus_di > minus_di, 1, -1), index=df.index)
    return SignalOutput(score=score, side=side)


def volume_price_divergence(df: pd.DataFrame) -> SignalOutput:
    """Volume spike with flat price => accumulation (long-bias). Inverse for distribution."""
    vol_z = _zclip(df["volume"])
    ret_abs = df["close"].pct_change().abs()
    ret_z = _zclip(ret_abs)
    accumulation = (vol_z > 2.0) & (ret_z < 0.5)
    distribution = (vol_z > 2.0) & (df["close"].pct_change() < 0) & (ret_z > 1.5)
    score = pd.Series(0.0, index=df.index)
    score[accumulation] = 0.6
    score[distribution] = 0.6
    side = pd.Series(0, index=df.index, dtype=int)
    side[accumulation] = 1
    side[distribution] = -1
    return SignalOutput(score=score, side=side)


def external_event_signal(df: pd.DataFrame, events: pd.DataFrame, decay_bars: int = 6,
                          strength: float = 1.0) -> SignalOutput:
    """Plug-in for X/listing/whale events loaded from CSV.

    events DataFrame: columns [time, coin, side, weight] where side in {-1, 0, 1}.
    Score decays linearly over `decay_bars` after the event.
    """
    score = pd.Series(0.0, index=df.index)
    side = pd.Series(0, index=df.index, dtype=int)
    if events.empty:
        return SignalOutput(score=score, side=side)
    ev = events[events["coin"] == df["coin"].iloc[0]].copy() if "coin" in df.columns else events.copy()
    if ev.empty:
        return SignalOutput(score=score, side=side)
    ev["time"] = pd.to_datetime(ev["time"], utc=True)
    for _, row in ev.iterrows():
        t = row["time"]
        if t not in df.index:
            # snap to next bar
            future = df.index[df.index >= t]
            if len(future) == 0:
                continue
            t = future[0]
        i = df.index.get_loc(t)
        w = float(row.get("weight", 1.0)) * strength
        s = int(row.get("side", 1))
        for k in range(decay_bars):
            j = i + k
            if j >= len(df):
                break
            decay = max(0.0, 1.0 - k / decay_bars)
            new_score = min(1.0, score.iloc[j] + w * decay)
            if new_score > score.iloc[j]:
                score.iloc[j] = new_score
                side.iloc[j] = s
    return SignalOutput(score=score, side=side)


# ---- registry --------------------------------------------------------------

SIGNAL_REGISTRY: dict[str, SignalFn] = {
    "volume_zscore": volume_zscore_breakout,
    "momentum_breakout": momentum_breakout,
    "momentum_returns": momentum_returns,
    "funding_reversion": funding_mean_reversion,
    "funding_flip": funding_flip,
    "liq_cascade": liquidation_cascade_bounce,
    "vol_compression": volatility_compression_breakout,
    "trend_adx": trend_strength_adx_like,
    "vol_price_div": volume_price_divergence,
}


def apply_all(df: pd.DataFrame, signal_names: list[str] | None = None,
              external_events: pd.DataFrame | None = None) -> dict[str, SignalOutput]:
    names = signal_names or list(SIGNAL_REGISTRY.keys())
    out: dict[str, SignalOutput] = {}
    for n in names:
        if n not in SIGNAL_REGISTRY:
            continue
        out[n] = SIGNAL_REGISTRY[n](df)
    if external_events is not None and not external_events.empty:
        out["external"] = external_event_signal(df, external_events)
    return out
