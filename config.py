# ════════════════════════════════════════════════════════════════
#  config.py — NSE OI Dashboard v5.3
#  All tuneable constants live here. Edit only this file to
#  customise symbol, lot size, thresholds, and IV settings.
# ════════════════════════════════════════════════════════════════

# ── Symbol ──────────────────────────────────────────────────────
SYMBOL       = "NIFTY"     # "NIFTY" or "BANKNIFTY"
LOT_SIZE     = 65          # Nifty=65, BankNifty=30  (NSE revised Jan-2026 expiry)
REFRESH_RATE = 35          # Seconds between NSE calls (keep ≥ 35)
MAX_RETRIES  = 3

# ── File paths ───────────────────────────────────────────────────
import os
from datetime import datetime
_dt = datetime.now().strftime("%Y%m%d")
LOG_FILE      = f"{SYMBOL}_OI_{_dt}.csv"
PLOT_FILE     = f"{SYMBOL}_chart.png"
IV_HIST_FILE  = f"{SYMBOL}_iv_history.csv"   # rolling IV store for IVR/IVP

# ── PCR thresholds (from NsePcrSignal — auto-tuned during session) ──
PCR_BEARISH   = 1.20
PCR_BULLISH   = 0.80

# 5-level PCR signal thresholds (NsePcrSignal exact values)
PCR_THRESHOLDS = {
    "STRONG_BUY":  0.75,
    "BUY":         0.90,
    "SELL":        1.10,
    "STRONG_SELL": 1.30,
}
LOCALIZED_PCR_RANGE = 8    # ±8 strikes around ATM for localized PCR

# ── OI alert ─────────────────────────────────────────────────────
ROC_THRESHOLD = 50_000     # OI change per cycle to fire RoC alert (NIFTY default)
ROC_THRESHOLD_SYMBOL = {   # Per-symbol overrides — BANKNIFTY has ~4× less total OI
    "NIFTY":       60_000,   # ~scaled with new lot 65 vs old 50 (+30%)
    "BANKNIFTY":   25_000,   # ~scaled with new lot 30 vs old 15 (+100%)
    "FINNIFTY":    20_000,
    "MIDCPNIFTY":  12_000,
}

# ── Trade filter ─────────────────────────────────────────────────
MAX_TRADES_PER_DAY  = 3    # default; overridden per symbol below
MIN_SIGNAL_SCORE    = 55
MIN_TRADE_GAP_MINS  = 20   # default; overridden per symbol below

# Per-symbol trade caps and time gaps.
# BNF is faster/larger range — allow more entries with tighter re-entry gap.
# NF is slower/more choppy — conservative cap with longer gap.
MAX_TRADES_PER_DAY_SYMBOL = {
    "NIFTY":       3,    # conservative — NF often choppy
    "BANKNIFTY":   5,    # allow more on trending BNF days
    "FINNIFTY":    3,
    "MIDCPNIFTY":  3,
}
MIN_TRADE_GAP_MINS_SYMBOL = {
    "NIFTY":       20,   # 20 min between NF entries
    "BANKNIFTY":   15,   # 15 min between BNF entries (moves faster)
    "FINNIFTY":    20,
    "MIDCPNIFTY":  20,
}
# Same-bias retake: minimum score required to override the same-direction guard
SAME_BIAS_OVERRIDE_SCORE = 70   # was 65; stricter to prevent chasing

# Opening-session gate: no trades in the first N minutes after 09:15 IST.
# BNF lost 3 consecutive trades at 09:30-09:33 due to opening volatility.
NO_TRADE_BEFORE_MINS  = 20   # gate lifts at 09:35; 0 to disable

# Telegram rate-limiting for informational (not-taken) alerts.
# Taken signals ALWAYS send immediately; informational alerts are throttled.
INFO_ALERT_COOLDOWN_MINS = 30  # min gap between score≥70-but-not-taken alerts

# ── Technical indicators (haripm2211 StrategyEngine) ─────────────
RSI_PERIOD   = 14
VWAP_WINDOW  = 20
RSI_BULLISH  = 58    # was 55 — tighter bullish (prevent whipsaw)
RSI_BEARISH  = 42    # was 35 — more sensitive on trending bearish days

# ── IV Analytics (v5.3 NEW) ───────────────────────────────────────
IV_SKEW_OTM_STRIKES = 3    # How many OTM strikes to average for skew
IV_SKEW_PUT_HEAVY_THRESHOLD  = 2.0   # IV skew % above this = PUT HEAVY
IV_SKEW_CALL_HEAVY_THRESHOLD = -2.0  # IV skew % below this = CALL HEAVY
IV_HISTORY_DAYS = 252       # 1 trading year for IVR / IVP calculation
IV_DAILY_ALERT_SPIKE = 20   # % rise in ATM IV in one cycle to fire spike alert

# ── High-Probability Option Buying (v5.10) ──────────────────────
# Regime detector (signals/regime_detector.py)
REGIME_MIN_WINDOW   = 30    # minimum cycles before regime is computed
REGIME_SCORE_WEIGHT = 10    # max pts added to score for TRENDING regime

# Options math gate (signals/options_math.py)
MIN_PROB_ITM        = 0.35  # minimum P(ITM) to include a strike in recs
MIN_EV_PCT          = 10.0  # minimum EV%  to take a trade
TARGET_PREMIUM_MULT = 2.0   # exit at 2× premium (100% gain)
SL_PREMIUM_MULT     = 0.5   # stop at 0.5× premium (50% loss)

# Kelly position sizing (signals/kelly_sizing.py)
KELLY_HALF          = True  # use half-Kelly (recommended)
KELLY_MIN_TRADES    = 10    # minimum trades before Kelly departs from default

# Exit engine triggers (signals/kelly_sizing.py)
EXIT_IV_CRUSH_PCT   = 0.30  # exit if IV rose 30% since entry
EXIT_TRAIL_TRIGGER  = 0.50  # start trailing at +50% premium gain

# ── ML Ensemble Signal (signals/ml_signal.py) ───────────────────
ML_ENABLED          = True    # set False to disable ML factor entirely
ML_RETRAIN_CYCLES   = 100     # retrain after this many live cycles
ML_CONFIRM_THRESHOLD = 0.65   # probability above which → CONFIRM (+pts)
ML_CONTRA_THRESHOLD  = 0.38   # probability below which → CONTRA  (-pts)

# ── Demo / Display mode ──────────────────────────────────────────
DEMO_MODE    = None         # None=auto | True=force demo | False=force live
DISPLAY_MODE = "terminal"   # "terminal" | "tkinter"  (override with --gui flag)
