# ════════════════════════════════════════════════════════════════
#  state.py — Shared mutable session state
#  All modules import from here to avoid circular imports.
#  Reset between sessions with reset_day().
# ════════════════════════════════════════════════════════════════

from signals.indicators import StrategyEngine

# ── Spot price series for Hurst / realized-vol calculations ─────
spot_series: list = []          # rolling spot prices (last 200 cycles)
SPOT_SERIES_MAX = 200

# ── Open option positions (exit engine) ──────────────────────────
open_positions: list = []       # list of OpenPosition objects

# ── Kelly / per-session trade stats ─────────────────────────────
kelly_data: dict = {}           # latest kelly_sizing() result

# ── OI tracking ──────────────────────────────────────────────────
prev_oi:     dict = {}  # strike -> {"CE": oi, "PE": oi} — previous CYCLE OI for RoC
baseline_oi: dict = {}  # strike -> {"CE": oi, "PE": oi} — SESSION START OI for ΔOI chart

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
consecutive_bias_cycles: int = 0   # counts how many cycles in a row have same bias
_last_bias: str = "NEUTRAL"          # previous cycle's bias, for tracking


def reset_day():
    """Call at session start or when user presses 'New Day' in GUI."""
    global prev_oi, baseline_oi, signal_log, accuracy_window
    global daily_trades_taken, last_trade_time
    global pcr_bearish, pcr_bullish
    global consecutive_bias_cycles, _last_bias   # BUG-04 fix

    prev_oi                  = {}
    baseline_oi              = {}    # reset clears session-start reference
    signal_log               = []
    accuracy_window          = []
    daily_trades_taken       = 0
    last_trade_time          = None
    consecutive_bias_cycles  = 0    # BUG-04: was not reset → stale trend_persist score
    _last_bias               = "NEUTRAL"  # BUG-04
    strategy_engine.reset()

    from config import PCR_BEARISH, PCR_BULLISH
    pcr_bearish = PCR_BEARISH
    pcr_bullish = PCR_BULLISH
    global spot_series, open_positions, kelly_data
    spot_series   = []
    open_positions = []
    kelly_data    = {}
