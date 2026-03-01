# ════════════════════════════════════════════════════════════════
#  state.py — Shared mutable session state
#  All modules import from here to avoid circular imports.
#  Reset between sessions with reset_day().
# ════════════════════════════════════════════════════════════════

from signals.indicators import StrategyEngine

# ── OI tracking ──────────────────────────────────────────────────
prev_oi: dict = {}          # strike -> {"CE": oi, "PE": oi} for RoC detection

# ── Signal log ───────────────────────────────────────────────────
signal_log: list  = []      # all Signal objects (taken + skipped)
accuracy_window: list = []  # rolling 20-signal window for auto-tuner

# ── Trade filter counters ─────────────────────────────────────────
daily_trades_taken: int = 0
last_trade_time         = None  # datetime (IST-aware) of last taken trade

# ── Technical indicator engine ────────────────────────────────────
strategy_engine = StrategyEngine()

# ── Auto-tuner (mutable thresholds) ──────────────────────────────
# Initialised from config; auto_tune() modifies these at runtime
from config import PCR_BEARISH as _PB, PCR_BULLISH as _PBu
pcr_bearish: float = _PB
pcr_bullish: float = _PBu


def reset_day():
    """Call at session start or when user presses 'New Day' in GUI."""
    global prev_oi, signal_log, accuracy_window
    global daily_trades_taken, last_trade_time
    global pcr_bearish, pcr_bullish

    prev_oi             = {}
    signal_log          = []
    accuracy_window     = []
    daily_trades_taken  = 0
    last_trade_time     = None
    strategy_engine.reset()

    from config import PCR_BEARISH, PCR_BULLISH
    pcr_bearish = PCR_BEARISH
    pcr_bullish = PCR_BULLISH
