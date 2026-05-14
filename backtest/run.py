from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from .config import BacktestConfig, DEFAULT_UNIVERSE
from .data import build_dataset, now_ms
from .engine import run_backtest, run_each_signal_isolated
from .metrics import (perf_stats, per_signal_summary, per_symbol_summary,
                      trades_to_frame, walk_forward_eval)
from .signals import apply_all, SIGNAL_REGISTRY


def _load_external_events(path: str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists():
        print(f"external events file {path} not found, skipping", file=sys.stderr)
        return pd.DataFrame()
    df = pd.read_csv(p)
    required = {"time", "coin", "side"}
    if not required.issubset(df.columns):
        raise SystemExit(f"external events CSV missing columns: {required - set(df.columns)}")
    if "weight" not in df.columns:
        df["weight"] = 1.0
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hyperliquid signal backtest")
    p.add_argument("--symbols", nargs="+", default=None,
                   help="Symbols (default: tier A+B). Use 'ALL' to query HL.")
    p.add_argument("--interval", default="1h", choices=["15m", "1h", "4h"])
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--risk", type=float, default=0.01, help="risk per trade as fraction of equity")
    p.add_argument("--sl-atr", type=float, default=2.0)
    p.add_argument("--tp-r", type=float, default=2.5)
    p.add_argument("--time-stop", type=int, default=48)
    p.add_argument("--entry-threshold", type=float, default=0.6)
    p.add_argument("--exit-threshold", type=float, default=0.3)
    p.add_argument("--max-positions", type=int, default=5)
    p.add_argument("--no-shorts", action="store_true")
    p.add_argument("--signals", nargs="+", default=None,
                   help=f"Signals to enable. Available: {list(SIGNAL_REGISTRY)}")
    p.add_argument("--mode", choices=["per-signal", "combined", "both"], default="both")
    p.add_argument("--walk-forward", type=int, default=4)
    p.add_argument("--external-events", default=None,
                   help="CSV with cols time,coin,side[,weight] for X/listing/whale events")
    p.add_argument("--cache-dir", default=".bt_cache")
    p.add_argument("--out-dir", default="bt_reports")
    p.add_argument("--weights", default=None,
                   help='JSON dict for combined-mode signal weights, e.g. \'{"momentum_returns":2,"funding_reversion":0.5}\'')
    args = p.parse_args(argv)

    cfg = BacktestConfig(
        symbols=list(args.symbols) if args.symbols else list(DEFAULT_UNIVERSE),
        interval=args.interval,
        lookback_days=args.days,
        initial_equity_usd=args.equity,
        risk_per_trade=args.risk,
        max_concurrent_positions=args.max_positions,
        taker_fee_bps=2.5,
        slippage_bps=5.0,
        enable_shorts=not args.no_shorts,
        sl_atr_mult=args.sl_atr,
        tp_r_multiple=args.tp_r,
        time_stop_bars=args.time_stop,
        entry_threshold=args.entry_threshold,
        exit_threshold=args.exit_threshold,
        cache_dir=args.cache_dir,
        walk_forward_folds=args.walk_forward,
    )

    if cfg.symbols == ["ALL"]:
        from .data import get_universe
        cfg.symbols = get_universe()
        print(f"Loaded {len(cfg.symbols)} symbols from HL")

    end_ms = now_ms()
    start_ms = end_ms - cfg.lookback_days * 86400 * 1000

    print(f"Fetching {len(cfg.symbols)} symbols × {cfg.interval} × {cfg.lookback_days}d "
          f"(cache: {cfg.cache_dir})…")
    t0 = time.time()
    data = build_dataset(cfg.symbols, cfg.interval, start_ms, end_ms, cfg.cache_dir)
    print(f"  loaded {len(data)} symbols with data in {time.time()-t0:.1f}s")
    if not data:
        print("No data — aborting.", file=sys.stderr)
        return 2

    events_df = _load_external_events(args.external_events)
    signal_names = args.signals if args.signals else list(SIGNAL_REGISTRY.keys())
    print(f"Computing signals: {signal_names}{' + external' if not events_df.empty else ''}…")
    sigs_by_coin = {}
    for coin, df in data.items():
        ev = events_df[events_df["coin"] == coin] if not events_df.empty else None
        sigs_by_coin[coin] = apply_all(df.assign(coin=coin), signal_names, ev)

    weights = json.loads(args.weights) if args.weights else None

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Per-signal isolated mode ----
    if args.mode in ("per-signal", "both"):
        print("\n=== Per-signal isolated backtests ===")
        per_results = run_each_signal_isolated(data, sigs_by_coin, cfg)
        summary = per_signal_summary(per_results)
        print(tabulate(summary, headers="keys", tablefmt="github", showindex=False))
        summary.to_csv(out_dir / "per_signal_summary.csv", index=False)
        # dump trades per signal
        for name, res in per_results.items():
            tdf = trades_to_frame(res.trades)
            if not tdf.empty:
                tdf.to_csv(out_dir / f"trades_{name}.csv", index=False)

    # ---- Combined mode ----
    if args.mode in ("combined", "both"):
        print("\n=== Combined-signal backtest ===")
        combined_res = run_backtest(data, sigs_by_coin, cfg, weights=weights, per_signal_mode=False)
        s = perf_stats(combined_res)
        print(tabulate([s.__dict__], headers="keys", tablefmt="github"))
        tdf = trades_to_frame(combined_res.trades)
        if not tdf.empty:
            tdf.to_csv(out_dir / "trades_combined.csv", index=False)
            print("\nTop-PnL trades:")
            top = tdf.sort_values("pnl", ascending=False).head(10)[
                ["symbol", "signal", "side", "entry_time", "exit_time", "pnl",
                 "r_multiple", "exit_reason", "score_at_entry"]]
            print(tabulate(top, headers="keys", tablefmt="github", showindex=False))
            print("\nWorst trades:")
            bot = tdf.sort_values("pnl").head(10)[
                ["symbol", "signal", "side", "entry_time", "exit_time", "pnl",
                 "r_multiple", "exit_reason", "score_at_entry"]]
            print(tabulate(bot, headers="keys", tablefmt="github", showindex=False))
            print("\nPer-symbol:")
            sym = per_symbol_summary(combined_res)
            print(tabulate(sym, headers="keys", tablefmt="github"))
            sym.to_csv(out_dir / "per_symbol_summary_combined.csv")
            # attribution per dominant signal in combined run
            attrib = tdf.groupby("signal").agg(
                n=("pnl", "size"),
                hit_rate=("pnl", lambda x: float((x > 0).mean())),
                total_pnl=("pnl", "sum"),
                avg_r=("r_multiple", "mean"),
            ).sort_values("total_pnl", ascending=False)
            print("\nAttribution (combined trades by dominant signal):")
            print(tabulate(attrib.round(3), headers="keys", tablefmt="github"))
            attrib.to_csv(out_dir / "attribution_combined.csv")
        combined_res.equity_curve.to_csv(out_dir / "equity_curve_combined.csv")

        # walk-forward on combined strategy
        if cfg.walk_forward_folds >= 2:
            print("\n=== Walk-forward (combined) ===")
            wf = walk_forward_eval(
                data, sigs_by_coin, cfg,
                runner=lambda d, s, c: run_backtest(d, s, c, weights=weights, per_signal_mode=False),
                folds=cfg.walk_forward_folds,
            )
            print(tabulate(wf, headers="keys", tablefmt="github", showindex=False))
            wf.to_csv(out_dir / "walk_forward_combined.csv", index=False)

    print(f"\nReports written to: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
