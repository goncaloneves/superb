from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# HL candleSnapshot caps the result at ~5000 bars; we page in windows.
_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def _post(payload: dict, retries: int = 4, timeout: int = 30) -> dict | list:
    delay = 1.0
    last_err = None
    for _ in range(retries):
        try:
            r = requests.post(HL_INFO_URL, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"HL request failed after retries: {last_err}; payload={payload}")


def get_universe() -> list[str]:
    meta = _post({"type": "meta"})
    return [u["name"] for u in meta["universe"]]


def fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    step_ms = _INTERVAL_MS[interval] * 4500  # safety margin under the 5000-bar cap
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(cursor + step_ms, end_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": cursor, "endTime": window_end},
        }
        data = _post(payload)
        if not data:
            cursor = window_end
            continue
        rows.extend(data)
        cursor = data[-1]["T"] + 1
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={"t": "open_ms", "T": "close_ms", "o": "open", "c": "close",
                            "h": "high", "l": "low", "v": "volume", "n": "trades"})
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["open_ms"]).sort_values("open_ms").reset_index(drop=True)
    df["coin"] = coin
    return df[["time", "open_ms", "close_ms", "coin", "open", "high", "low", "close", "volume", "trades"]]


def fetch_funding(coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    # fundingHistory paginates by startTime; HL returns funding events ~hourly.
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        payload = {"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end_ms}
        data = _post(payload)
        if not data:
            break
        rows.extend(data)
        last_t = data[-1]["time"]
        if last_t <= cursor:
            break
        cursor = last_t + 1
        if len(data) < 500:
            break
    if not rows:
        return pd.DataFrame(columns=["time", "coin", "funding_rate", "premium"])
    df = pd.DataFrame(rows)
    df["funding_rate"] = df["fundingRate"].astype(float)
    df["premium"] = df["premium"].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df[["time", "coin", "funding_rate", "premium"]].drop_duplicates(subset=["time"]).sort_values("time")


def _cache_path(cache_dir: str, kind: str, coin: str, interval: str, start_ms: int, end_ms: int) -> Path:
    p = Path(cache_dir) / kind
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{coin}_{interval}_{start_ms}_{end_ms}.parquet"


def load_or_fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int, cache_dir: str) -> pd.DataFrame:
    path = _cache_path(cache_dir, "candles", coin, interval, start_ms, end_ms)
    if path.exists():
        return pd.read_parquet(path)
    df = fetch_candles(coin, interval, start_ms, end_ms)
    if not df.empty:
        df.to_parquet(path)
    return df


def load_or_fetch_funding(coin: str, start_ms: int, end_ms: int, cache_dir: str) -> pd.DataFrame:
    path = _cache_path(cache_dir, "funding", coin, "1h", start_ms, end_ms)
    if path.exists():
        return pd.read_parquet(path)
    df = fetch_funding(coin, start_ms, end_ms)
    if not df.empty:
        df.to_parquet(path)
    return df


def build_dataset(coins: Iterable[str], interval: str, start_ms: int, end_ms: int,
                  cache_dir: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for coin in coins:
        candles = load_or_fetch_candles(coin, interval, start_ms, end_ms, cache_dir)
        if candles.empty:
            continue
        funding = load_or_fetch_funding(coin, start_ms, end_ms, cache_dir)
        df = candles.copy()
        if not funding.empty:
            f = funding[["time", "funding_rate"]].rename(columns={"time": "funding_time"})
            df = pd.merge_asof(df.sort_values("time"), f.sort_values("funding_time"),
                               left_on="time", right_on="funding_time", direction="backward")
            df["funding_rate"] = df["funding_rate"].fillna(0.0)
            df = df.drop(columns=["funding_time"])
        else:
            df["funding_rate"] = 0.0
        df = df.set_index("time")
        out[coin] = df
    return out


def now_ms() -> int:
    return int(time.time() * 1000)
