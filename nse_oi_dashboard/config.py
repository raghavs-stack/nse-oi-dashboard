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

# ── Demo / Display mode ──────────────────────────────────────────
DEMO_MODE    = None         # None=auto | True=force demo | False=force live
DISPLAY_MODE = "terminal"   # "terminal" | "tkinter"  (override with --gui flag)
