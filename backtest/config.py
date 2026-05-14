from dataclasses import dataclass, field
from typing import Sequence


DEFAULT_UNIVERSE_TIER_A: tuple[str, ...] = ("BTC", "ETH", "SOL")
DEFAULT_UNIVERSE_TIER_B: tuple[str, ...] = (
    "BNB", "XRP", "DOGE", "AVAX", "LINK", "ARB", "OP", "SUI", "APT", "TIA",
    "INJ", "SEI", "WIF", "ENA", "PEPE", "BONK",
)
DEFAULT_UNIVERSE = DEFAULT_UNIVERSE_TIER_A + DEFAULT_UNIVERSE_TIER_B


@dataclass
class BacktestConfig:
    # universe & data
    symbols: Sequence[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    interval: str = "1h"
    lookback_days: int = 180

    # execution
    initial_equity_usd: float = 10_000.0
    risk_per_trade: float = 0.01          # fraction of equity risked at SL
    max_concurrent_positions: int = 5
    max_position_notional_frac: float = 0.25  # cap per-position notional
    taker_fee_bps: float = 2.5            # 0.025%
    slippage_bps: float = 5.0             # 0.05% per side on long-tail; lower for BTC/ETH
    enable_shorts: bool = True

    # risk
    sl_atr_mult: float = 2.0              # SL = entry - k*ATR
    tp_r_multiple: float = 2.5            # TP at +k*R from entry
    time_stop_bars: int = 48              # flatten if no TP/SL within N bars
    signal_decay_exit: bool = True        # exit if originating signal score falls below threshold
    daily_dd_kill_frac: float = 0.03      # pause new entries after -3% day

    # signal gates
    entry_threshold: float = 0.6          # combined score must exceed
    exit_threshold: float = 0.3           # below this triggers signal-decay exit
    min_24h_volume_usd: float = 1_000_000 # liquidity gate
    funding_extreme_pct_per_hour: float = 0.0005  # 0.05%/h => skip longs

    # data caching
    cache_dir: str = ".bt_cache"

    # walk-forward
    walk_forward_folds: int = 4
