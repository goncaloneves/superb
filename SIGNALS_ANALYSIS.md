# Crypto Signals → Hyperliquid Trading Bot — Deep Dive

> Branch: `claude/crypto-trading-signals-analysis-dkvpH`
> Goal: collect signals → enter on Hyperliquid → take profit / leave with stop loss.
> This is a strategy & architecture analysis, not code. It is meant to be the
> spec we build from.

---

## 1. Frame the problem first

Before picking signals, fix what we're actually trading. Hyperliquid constrains
the universe and the mechanics.

### Hyperliquid (HL) reality check
- **Venue:** L1 perp DEX. ~150+ perp markets, BTC/ETH dominate volume, long
  tail of mid/low-cap perps (often listed within hours/days of a notable CEX
  listing or a Twitter narrative spike).
- **Order types:** market, limit, stop-market, stop-limit, TP/SL, IOC, ALO,
  TWAP. Bracket orders supported — important for our entry+SL+TP atomicity.
- **Latency:** WebSocket order book + fills are <100 ms typical from a
  co-located VPS; HTTP order placement ~50–200 ms. Block time ≈ 0.07–0.2 s.
  This is *fast enough* for narrative trades, *not fast enough* to beat HFT
  on a Binance listing announcement (Binance HFT sees the listing tweet in
  tens of ms — we will lose that race).
- **Fees:** ~0.025% taker / 0.005% maker after volume tiers; *funding* is paid
  hourly, can be brutal on hot longs (annualized 50–500%+ on meme perps).
- **Liquidity tiers:**
  - Tier A (BTC, ETH, SOL): deep, low slippage even at $100k+ clip
  - Tier B (top-30 alts): OK to $25k clip, watch for funding shocks
  - Tier C (long-tail meme perps): $1–5k clip, wide spread, frequent
    liquidation cascades, funding can flip ±10% / 8h
- **Risk constraints:** auto-deleveraging exists; isolated vs. cross matters.
  For a signal bot, **isolated margin per position** is the sane default.

The asymmetry: HL is where price often *trails* CEX listing news but *leads*
on Crypto Twitter narrative trades (because HL has perps for tokens that
Binance/Coinbase don't list yet). So the bot's edge surface is biggest where
HL has a perp **and** the asset is not yet on Binance spot — that's where
listing-anticipation and narrative-momentum signals pay best.

---

## 2. Signal taxonomy — what works, what doesn't

For each signal I'll grade on four axes:

- **Edge** — how much excess return after fees/funding (★ poor → ★★★★★ strong)
- **Latency to act** — how fast you must move from signal → fill
- **Capacity** — how much $ can deploy before slippage eats edge
- **False-positive rate** — how noisy the signal is

### 2.1 Twitter / X signals — ★★★★ edge, ms–min latency, low capacity

**What actually works:**

| Sub-signal | Edge | Notes |
|---|---|---|
| Curated KOL list mentions | ★★★★ | A small whitelist (50–200 accounts) of operators with a *backtested* lead-to-pump record. Public "follow Murad" type lists are already priced in. |
| Tier-1 account first-mention | ★★★★★ | An account that has *never* tweeted about token X suddenly tweeting X, especially if account has a history of pre-pump mentions. This is the highest-edge X signal. |
| Volume of unique mentions (LunarCrush/Kaito-style) | ★★★ | Lagging but works for confirmation; useful as filter not trigger. |
| Sentiment classification | ★★ | Mostly priced in. Useful only as a tiebreaker. |
| Founder / project account posts | ★★★ | Roadmap, partnership, mainnet tweets. Often pumps 5–15% on launch tweet. |
| Exchange official account tweets | ★★★★★ | "We are pleased to announce…" — but you will be racing HFT bots. See §2.4. |

**Implementation choices for X ingestion:**
- **Official X API v2 filtered stream** — reliable, but rate-limited and not
  cheap ($200/mo Basic, $5k/mo Pro). Latency is fine (1–3 s).
- **Scrape via Nitter mirrors / unofficial APIs** — flaky, gets cut off,
  not recommended in production.
- **Webhook services (TweetScout, Phantom, Apify)** — pragmatic middle
  ground; ~1–5 s latency, $50–500/mo.
- **NLP layer:** ticker extraction (`$BONK`, `$WIF`), CA (contract address)
  detection, sentiment + spam filter. Most "$X" mentions are noise — must
  cross-reference against HL's listed symbols.

**Gotchas:**
- Cashtag collisions: `$SOL` could mean Solana or a microcap with the same
  ticker. Resolve by contract address or by an account's prior history.
- Sponsored/paid shills: account behavior changes can be detected (sudden
  posting of tickers an account never mentioned).
- Time-zone clusters: US KOL accounts posting at 9am ET cluster pumps.

### 2.2 Volume signals — ★★★ edge, sec–min latency, high capacity

The most robust and least gameable category.

| Sub-signal | Description |
|---|---|
| **Volume z-score breakout** | Rolling 1h / 24h volume crosses N stdev above 7d mean. Works on BTC/ETH/large alts as a momentum trigger. |
| **Volume + price divergence** | Volume spikes with flat price = accumulation; spike with price = breakout. The first one is the higher-edge setup. |
| **CEX vs DEX volume ratio** | If DEX volume rises faster than CEX volume, retail/narrative is the driver — usually meme-coin behavior. |
| **HL-specific OI delta** | Open interest rising fast on HL perp while spot is muted = bot/whale positioning. Big leading indicator on long-tail. |
| **Taker buy/sell imbalance** | Aggressive market-buy share over 30m windows; classic order-flow alpha. |

**Capacity:** can size in size on Tier A/B perps because volume signals
correlate with liquidity. Best foundational signal for the bot.

### 2.3 Market cap / market-structure signals — ★★ edge, hour–day latency

Slow signals — useful as *filters*, not as *triggers*.

- **Mcap rank changes** (e.g. CMC top-100 entry) — slow, mostly priced in.
- **Mcap / FDV ratio** — low circulating supply pumps explode and dump
  harder. Use as a *risk filter* (avoid extreme low-circ unless you're
  scalping).
- **Sector rotation** (AI tokens, DePIN, L1s rotating) — track sector
  index returns (CoinGecko categories) and overweight signals in the
  leading sector.
- **BTC dominance regime** — when BTC.D falls, alt signals get more weight;
  when BTC.D rises, fade alt longs. Critical macro overlay.

### 2.4 Exchange-listing signals — ★★★★★ edge, ms–s latency, *competitive*

Listing pumps are the single biggest discrete event in crypto microstructure.

**Listing event types and HL playability:**

| Event | HL playable? | Typical move | Notes |
|---|---|---|---|
| Binance spot announcement | If HL has perp before BN announces | +30–80% in minutes | Race condition with HFT — you need direct tweet stream + sub-second fill. |
| Binance perp listing | Often HL already has perp | +10–30% | Smaller move, but funding flip from negative → positive is tradeable. |
| Coinbase roadmap add | Usually HL has perp | +15–40% | Slower bleed; can scale in. |
| Upbit (KRW) listing | Often surprise | +20–60% | Korean session; needs Korean-hour bot uptime. |
| Binance Futures listing | Sometimes HL already lists | +5–15% | Often *fades* — sell the news. |
| Binance delisting / monitoring tag | HL still lists | -10–30% | Short signal. |
| HL itself listing a new perp | n/a — direct | Variable | HL listings sometimes pump on the listing itself; track HL meta endpoint. |

**Sources (cheapest → fastest):**
1. Official exchange Twitter (rate-limited, race against everyone)
2. Exchange announcement page scraping (sometimes leaks 1–2 s before tweet)
3. Exchange API new-symbol detection (e.g. polling `/exchangeInfo` — sometimes
   symbols appear before the public tweet; this has historically been *the*
   alpha)
4. Wallet pre-funding monitoring (track exchange-labeled wallets receiving
   tokens — leading indicator)

**Realistic stance:** we will not beat the top HFT bots on raw Binance
listing speed. But we don't need to — HL's edge is the *adjacent* trades:
- "X just got listed on Coinbase → similar narrative token Y on HL has
  perp → buy Y on the rotation."
- "Binance listed perp for Z → HL perp Z funding is still flat → arb
  funding before it normalizes."

### 2.5 On-chain / whale signals — ★★★★ edge, sec–min latency

| Signal | Notes |
|---|---|
| Smart-money wallet buys | Curated list of profitable wallets (Arkham, Nansen, Hyperdash, or self-built from past PnL). Buys → leading indicator on micro-caps. |
| CEX inflow/outflow | Big inflow to Binance = sell pressure; big outflow = accumulation. Useful as a confirm. |
| Stablecoin minting (USDT/USDC) | Macro liquidity proxy; mints precede BTC pumps. |
| HL leaderboard whale positions | HL exposes whale positions — track top 50 PnL accounts. When the same whale opens a new position in size, that's directly tradeable. **This is HL-native alpha.** |
| Token unlocks | Bearish — fade longs around unlock dates (TokenUnlocks data). |

The **HL whale-leaderboard signal** deserves special call-out: HL publishes
top trader positions semi-publicly. A bot that mirrors a curated set of
profitable HL whales (after filtering for survivorship & sample size) is
arguably the lowest-effort high-edge strategy on HL specifically.

### 2.6 Hyperliquid-native microstructure — ★★★ edge

These are signals you can only get if you're already on HL:

- **Funding rate extremes** — sustained >0.05%/h funding signals
  overcrowded longs; mean-reversion short. Inverse for shorts.
- **Liquidation cascades** — when liquidation volume in a 1m window
  > 3σ, the dust-settles bounce is tradeable (counter-trend long after
  a long-liquidation cascade, ~5–15 min hold).
- **HLP vault PnL** — the HL liquidity vault's PnL inflection can signal
  whether the market is grinding through liquidity or trending.
- **OI / price divergence** — OI rising on falling price = shorts piling
  in; squeeze potential.

### 2.7 News / Telegram / Discord — ★★★ edge, var. latency

- Premium TG alpha channels — often paid, often shill, but a small subset
  consistently leads.
- News aggregators (CryptoPanic, The Block API) — slower than X but cleaner.
- Discord bots in protocol servers for treasury/governance moves.

These are best as *enrichment*, not standalone triggers.

---

## 3. Signal fusion — how to combine them

A single signal is noisy. The bot's job is to **score** and **gate**.

### Suggested model

```
score(token, t) =
    w_x   * X_signal(token, t)        // 0–1, decays over minutes
  + w_vol * volume_signal(token, t)   // 0–1, z-score normalized
  + w_oi  * oi_delta(token, t)
  + w_whale * whale_signal(token, t)
  + w_list * listing_proximity(token, t)
  - p_funding * funding_pain(token, t)  // penalty for extreme funding
  - p_unlock  * unlock_proximity(token, t)
```

Gates (hard filters before any entry):
- HL has a perp for this token
- Spread < X bps
- 24h volume > $threshold (to ensure exit liquidity)
- Funding not in extreme zone against our direction
- Not within N hours of token unlock
- BTC 1h not in a -3σ down move (correlation risk)

Trigger:
- `score > entry_threshold` for ≥ K consecutive ticks (avoid flicker)
- Optional: confirm with 1m price > VWAP

### Weights: learned, not guessed

Start with equal weights, then fit on a backtest of 6–12 months of historical
signals. Two practical tactics:
1. **Logistic regression** predicting "did price hit +2R within 60m before
   -1R?" — labels you can compute from minute candles.
2. **Per-signal calibration** — measure each signal's standalone hit-rate
   and use that as the prior.

Avoid overfit: walk-forward validation, ≥6mo out-of-sample, expect 30–50%
of in-sample edge to vanish live.

---

## 4. Entry / exit / risk — the actual trade

### Entry
- **Order type:** marketable limit (limit at best ask + N bps), with
  IOC fallback. Pure market orders eat slippage on long-tail perps.
- **Position sizing:** Kelly-fraction style, capped. Concretely:
  `size = min(account_equity * risk_per_trade / sl_distance, max_clip)`
  where `risk_per_trade` = 0.5–1% of equity.
- **Slippage budget:** abort the entry if expected slippage > X% of the
  expected edge. (A signal worth +3% is not worth taking with 1% slippage
  and 0.5% funding cost.)
- **Bracket on entry:** submit TP + SL atomically (HL supports this) so
  there is *never* a naked position if our process dies.

### Stop loss — non-negotiable
- **ATR-based SL:** SL = entry − k × ATR(14, 1m). k ≈ 1.5–2.5 for momentum,
  tighter for mean-reversion.
- **Time stop:** if not in profit after T minutes, flatten. Most signal
  edges decay fast — holding a stale signal is how you bleed.
- **Funding stop:** if funding paid on this position exceeds X% of
  expected edge, flatten. HL funding can eat momentum trades alive.
- **Hard portfolio kill switch:** -3% daily PnL → pause all entries for
  the rest of the UTC day.

### Take profit
- **R-multiple TP ladder:** 50% off at +1R, 30% at +2R, 20% trails at
  +1R chandelier. Empirically this beats single-target exits on noisy
  signals.
- **Signal-decay TP:** if the originating signal's score drops below
  exit_threshold while in profit, take profit. (The signal is what got
  you in; if it's gone, your reason to hold is gone.)

### Position management
- One position per token, ever. No averaging down.
- Max N concurrent positions (start with 3–5).
- Correlation cap: don't open a 5th meme-perp long if BTC.D is rising.

---

## 5. Architecture

```
                     ┌──────────────────────────┐
                     │  Ingestion (per source)   │
                     │  X stream, HL ws,         │
                     │  Binance ws, on-chain,    │
                     │  CEX announcement scraper │
                     └─────────────┬─────────────┘
                                   │  raw events
                                   ▼
                     ┌──────────────────────────┐
                     │  Normalizer + dedup       │
                     │  (token resolution, CA,   │
                     │  cashtag disambiguation)  │
                     └─────────────┬─────────────┘
                                   ▼
                     ┌──────────────────────────┐
                     │  Feature store (Redis or  │
                     │  Timescale): per-token    │
                     │  rolling windows          │
                     └─────────────┬─────────────┘
                                   ▼
                     ┌──────────────────────────┐
                     │  Signal scorer            │
                     │  (computes score, gates)  │
                     └─────────────┬─────────────┘
                                   ▼
                     ┌──────────────────────────┐
                     │  Risk manager             │
                     │  (sizing, correlation,    │
                     │  kill switch, funding)    │
                     └─────────────┬─────────────┘
                                   ▼
                     ┌──────────────────────────┐
                     │  HL execution            │
                     │  (bracket orders,        │
                     │  reconciliation, retries)│
                     └──────────────────────────┘
```

**Tech opinions:**
- **Language:** TypeScript or Python. TS if you want the same code in
  ingestion + UI; Python if you want pandas/sklearn for research. A hybrid
  (Python research → TS prod) is normal.
- **State:** Redis for hot rolling-window state; Postgres/Timescale for
  historical bars + signal/trade log; S3 for raw event archive (for
  re-running backtests against actual historical X data, which is hard
  to re-obtain).
- **HL client:** official Python/TS SDKs, but write your own thin wrapper
  for retries, idempotency keys, and reconciliation. Network blips will
  happen.
- **Deploy:** single VPS in AWS us-east-1 or Tokyo (close to HL infra).
  Don't over-engineer with k8s for v1.
- **Observability:** every signal, every score, every order, logged with
  correlation IDs. You need this for postmortems, and there will be many.

---

## 6. Backtesting / forward-testing

This is where most signal bots die quietly.

### Backtest pitfalls specific to this strategy
- **Survivorship bias:** the meme tokens you remember are the ones that
  pumped. Build the signal universe from a *point-in-time* snapshot
  (HL's listed perps as of date T), not from today's list.
- **Lookahead in X data:** archived tweets often have updated like counts
  / quote counts. Use *creation-time* metadata only.
- **Listing leaks in historical CEX data:** sometimes the historical
  `exchangeInfo` JSON is unrecoverable — keep an immutable archive going
  forward.
- **Funding cost:** simulate funding payments per-hour, not just at entry.

### Recommended phases

1. **Paper backtest (offline):** 6–12 months of historical signals,
   minute bars. Goal: prove >1.5 Sharpe net of fees + funding + 0.1%
   slippage assumption.
2. **Paper forward (live signals, no orders):** 2–4 weeks. Goal:
   verify live signal latencies + signal/return distribution matches
   backtest.
3. **Tiny live ($100–500):** 2–4 weeks. Goal: catch execution bugs,
   verify slippage assumption, measure real fill quality.
4. **Scaled live:** scale by Kelly fraction with hard equity caps. Stop
   scaling on drawdown.

---

## 7. Risks / pitfalls (failure modes I'd budget for)

- **Funding decay is the silent killer.** A "winning" signal that holds
  too long on a 200% APR funding pair loses to funding even if it's
  green on price. Bake funding into expected value at entry.
- **Liquidation cascades cut both ways.** A SL inside the liquidation
  zone gets filled at terrible prices. Place SL *outside* the most recent
  liquidation cluster, or use guaranteed stops where available.
- **X signal poisoning.** Once a strategy is profitable, KOLs will tweet
  to bait it. Defenses: weight by historical lead-time accuracy of each
  account, decay weights after each shill, blacklist clear pumpers.
- **HL listing surprises.** A token's perp can be delisted or
  parameters changed (max leverage, funding cap). Read HL announcements;
  subscribe to the HL changelog.
- **Self-impact.** As size grows, your own buys move price → backtest
  Sharpe is unreachable at scale. Capacity-test by paper-trading at
  3–5× size.
- **Operational risk.** Key compromised, RPC down, signal lag spikes
  during volatility (exactly when you need it). Have a panic-flatten
  endpoint reachable from your phone.
- **Tax / compliance.** Perp PnL is reportable. Talk to a tax person
  before you scale.

---

## 8. Pragmatic 4-week roadmap

| Week | Deliverable |
|---|---|
| 1 | HL connector + paper exec; ingest HL OHLCV/OI/funding/whale leaderboard. Backtest framework skeleton. |
| 2 | X ingestion (paid API) + cashtag/CA resolver + KOL whitelist (start with 50 accounts). Volume z-score and OI-delta signals live in feature store. |
| 3 | Signal scorer + gates + risk manager. Backtest on 6mo data. Paper-forward for 7 days. |
| 4 | Listing-announcement scraper + on-chain whale module. Tiny live ($200) with one strategy enabled. Daily PnL + signal-attribution dashboard. |

After week 4: iterate on signal weights from live data, then enable
additional strategies one at a time.

---

## 9. My honest recommendation on where to start

If we want the best edge-per-engineering-hour:

1. **HL whale-leaderboard mirroring** (§2.5) — lowest infra cost, no NLP
   needed, directly uses HL-native data. Start here.
2. **Volume z-score + OI-delta** (§2.2) — generalist momentum that works
   on Tier A/B perps with size.
3. **Curated KOL X stream** (§2.1) — high edge but operationally heavier;
   layer on after #1 and #2 are stable.
4. **CEX listing scraper** (§2.4) — high edge but you'll lose the
   pure-speed race; play it as *adjacent rotation* trades on HL, not
   front-running Binance.
5. **Market cap / sector signals** (§2.3) — *filters* on top of the
   above, not standalone triggers.

The mistake to avoid: trying to ship all five at once. Each signal needs
its own backtest, its own latency budget, its own failure mode catalog.

---

## Open questions for you

1. **Budget for data feeds** (X API, on-chain APIs, listing scrapers)?
   Realistic floor is ~$300–800/mo to be competitive.
2. **Starting capital** — drives whether we can play Tier C long-tail
   (needs <$5k clips) or must stay Tier A/B.
3. **Hold horizon** — pure scalp (sec–min), swing (hour–day), or both?
   This changes signal weights significantly.
4. **Custody / keys** — running on a hot key in a VPS, or via an MPC
   service? Affects deploy story.
5. **Tolerance for drawdown** — Kelly sizing and signal aggressiveness
   should be set to your stomach, not mine.
