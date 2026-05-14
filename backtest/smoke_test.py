"""End-to-end wiring test with synthetic data. Validates signals + engine + metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.engine import run_backtest, run_each_signal_isolated
from backtest.metrics import per_signal_summary, perf_stats, trades_to_frame, per_symbol_summary
from backtest.signals import apply_all


def synth_series(coin: str, n: int = 1500, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    # geometric brownian motion with a few injected pumps + a downtrend leg
    rets = rng.normal(0.0001, 0.01, n)
    rets[200:210] += 0.03      # pump
    rets[700:710] -= 0.04      # crash
    rets[1100:1115] += 0.02    # secondary pump
    price = 100 * np.exp(np.cumsum(rets))
    high = price * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = price * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.r_[price[0], price[:-1]]
    vol = np.abs(rng.normal(1e6, 2e5, n))
    # spike volume on pump bars to trigger volume signals
    vol[200:210] *= 5
    vol[700:710] *= 4
    vol[1100:1115] *= 3
    funding = rng.normal(0, 0.00005, n)
    funding[300:400] = 0.0008  # sustained positive funding episode
    funding[800:900] = -0.0006
    df = pd.DataFrame({
        "open": open_, "high": np.maximum(high, np.maximum(open_, price)),
        "low": np.minimum(low, np.minimum(open_, price)),
        "close": price, "volume": vol, "funding_rate": funding,
        "coin": coin,
    }, index=t)
    return df


def main() -> int:
    data = {c: synth_series(c, seed=i + 1) for i, c in enumerate(["BTC", "ETH", "SOL"])}
    sigs = {coin: apply_all(df.assign(coin=coin)) for coin, df in data.items()}

    cfg = BacktestConfig(symbols=list(data), interval="1h", lookback_days=60,
                        initial_equity_usd=10_000, risk_per_trade=0.01,
                        entry_threshold=0.5, exit_threshold=0.2,
                        min_24h_volume_usd=0,  # synthetic
                        max_concurrent_positions=5)

    print("=== per-signal isolated ===")
    per = run_each_signal_isolated(data, sigs, cfg)
    summary = per_signal_summary(per)
    print(summary.to_string(index=False))

    print("\n=== combined ===")
    combined = run_backtest(data, sigs, cfg, per_signal_mode=False)
    s = perf_stats(combined)
    print(f"n={s.n_trades} hit={s.hit_rate:.2f} totalPnL={s.total_pnl:.2f} "
          f"sharpe={s.sharpe_daily:.2f} maxDD={s.max_drawdown_frac:.3f} "
          f"PF={s.profit_factor if s.profit_factor==float('inf') else round(s.profit_factor,2)}")
    tdf = trades_to_frame(combined.trades)
    if not tdf.empty:
        print("\nattribution by dominant signal:")
        attrib = tdf.groupby("signal").agg(n=("pnl","size"),
                                           hit=("pnl", lambda x: float((x>0).mean())),
                                           total_pnl=("pnl","sum"),
                                           avg_r=("r_multiple","mean")).round(3)
        print(attrib.to_string())
        print("\nexit reasons:")
        print(tdf["exit_reason"].value_counts().to_string())
    else:
        print("no trades produced (synthetic may not have crossed threshold)")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
