# Hyperliquid Signal Backtest

End-to-end backtest harness for evaluating which trading signals reliably
produce profit on Hyperliquid perps. Pulls real OHLCV + funding from the HL
Info API, computes a library of signals, and runs per-signal and combined
backtests with full SL/TP/funding/fees/slippage simulation.

Pairs with `../SIGNALS_ANALYSIS.md` (strategy spec).

## Install

```bash
pip install -r backtest/requirements.txt
```

## Quick start

```bash
# Tier A+B symbols, 1h bars, 180 days, isolated per-signal + combined
python -m backtest.run

# Specific symbols, shorter window
python -m backtest.run --symbols BTC ETH SOL --interval 1h --days 90

# Just one signal, custom risk
python -m backtest.run --signals momentum_returns --risk 0.005 --tp-r 3.0

# Use the entire HL perp universe (slow first run, then cached)
python -m backtest.run --symbols ALL --interval 4h --days 120
```

Reports land in `bt_reports/` (CSVs + console tables).

## What gets tested

Signals (in `signals.py`):

| Signal             | Hypothesis |
|--------------------|------------|
| `volume_zscore`    | Anomalous volume + directional candle = momentum start |
| `momentum_breakout`| Donchian-style N-bar high/low break |
| `momentum_returns` | N-bar return z-score continuation |
| `funding_reversion`| Mean-revert from sustained funding extremes |
| `funding_flip`     | Funding crosses zero = regime change |
| `liq_cascade`      | Counter-trend after high-volume capitulation bar |
| `vol_compression`  | Bollinger squeeze break |
| `trend_adx`        | Directional strength of established trend |
| `vol_price_div`    | High volume + flat price = accumulation |

External event signal (X mentions, exchange listings, whale prints) plugs
in via CSV — `--external-events events.csv` with columns
`time,coin,side[,weight]`.

## Output

For each run you get:

- `per_signal_summary.csv` — standalone per-signal metrics (hit-rate, R,
  Sharpe, max DD, profit factor, exit-reason mix). **This is the
  reliability check — which signals actually pay net of fees/funding.**
- `trades_<signal>.csv` — every isolated trade per signal.
- `trades_combined.csv` — every trade from the combined-score strategy,
  tagged by dominant signal at entry.
- `attribution_combined.csv` — PnL grouped by which signal was dominant.
- `per_symbol_summary_combined.csv` — PnL by coin.
- `walk_forward_combined.csv` — per-fold OOS metrics to flag overfit.
- `equity_curve_combined.csv` — bar-by-bar equity.

## Engine assumptions (read these)

- Fills at **next-bar open** with `slippage_bps` worse than mid.
- Taker fee on both sides (default 0.025%).
- Funding accrues per bar based on hourly funding rate × bar length ×
  notional × side (longs pay positive funding).
- SL/TP intra-bar; if both could trigger same bar, **SL wins** (conservative).
- Time stop = flatten after N bars without TP/SL.
- Signal-decay exit = flatten when originating signal score drops below
  `exit_threshold`.
- Day kill switch: after `-3%` day, no new entries that day.
- Position size = `risk_per_trade × equity / SL distance`, capped at
  `max_position_notional_frac × equity`.
- No look-ahead: signal at bar `i` is computed from data up through `i`;
  entry executes at bar `i+1` open.

## Adding external signals (X / listings / whale)

```csv
time,coin,side,weight
2025-03-04T12:00:00Z,WIF,1,1.0
2025-03-05T08:15:00Z,SOL,1,0.8
2025-03-06T19:00:00Z,PEPE,-1,1.0
```

`time` is the *event* time; the signal decays linearly over 6 bars after
it (configurable in `signals.external_event_signal`). Run with
`--external-events events.csv`.

For production: build this CSV from your X stream, Binance/Coinbase
announcement scraper, and HL whale leaderboard mirror — then re-backtest
the combined strategy with the external signal weighted in.

## Reading the results

A signal is "reliable" only if **all** these hold across walk-forward folds:

- `n_trades ≥ 30` per fold (statistical power)
- `profit_factor ≥ 1.3`
- `avg_r ≥ 0.15` (covers fees + slippage with margin)
- `sharpe_daily ≥ 1.0` net of funding
- `max_dd ≥ -0.20`
- Hit-rate consistent across folds (don't accept signals that worked only
  in one regime)

If only the combined run looks good but no individual signal does,
that's overfitting to interactions — don't trade it live.

## Caveats / known gaps

- HL public API does not give historical OI; OI-delta signals would need
  snapshotting (TODO). Funding history is available and used.
- Tier-C meme perps may have sparse early history; the data loader
  silently drops symbols with no candles.
- Slippage is a flat bps assumption; long-tail perps in real life suffer
  worse slippage on cascades — tighten `slippage_bps` for those tests.
- External events are point-in-time; if you backfill from today's data
  you'll get hindsight bias. Snapshot your event CSV at decision time.
