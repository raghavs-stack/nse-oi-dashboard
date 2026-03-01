# ════════════════════════════════════════════════════════════════
#  config.py — NSE OI Dashboard v5.3
#  All tuneable constants live here. Edit only this file to
#  customise symbol, lot size, thresholds, and IV settings.
# ════════════════════════════════════════════════════════════════

# ── Symbol ──────────────────────────────────────────────────────
SYMBOL       = "NIFTY"     # "NIFTY" or "BANKNIFTY"
LOT_SIZE     = 50          # Nifty=50, BankNifty=15
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
ROC_THRESHOLD = 50_000     # OI change per cycle to fire RoC alert

# ── Trade filter ─────────────────────────────────────────────────
MAX_TRADES_PER_DAY  = 3
MIN_SIGNAL_SCORE    = 55
MIN_TRADE_GAP_MINS  = 45

# ── Technical indicators (haripm2211 StrategyEngine) ─────────────
RSI_PERIOD   = 14
VWAP_WINDOW  = 20
RSI_BULLISH  = 55
RSI_BEARISH  = 35

# ── IV Analytics (v5.3 NEW) ───────────────────────────────────────
IV_SKEW_OTM_STRIKES = 3    # How many OTM strikes to average for skew
IV_SKEW_PUT_HEAVY_THRESHOLD  = 2.0   # IV skew % above this = PUT HEAVY
IV_SKEW_CALL_HEAVY_THRESHOLD = -2.0  # IV skew % below this = CALL HEAVY
IV_HISTORY_DAYS = 252       # 1 trading year for IVR / IVP calculation
IV_DAILY_ALERT_SPIKE = 20   # % rise in ATM IV in one cycle to fire spike alert

# ── Demo / Display mode ──────────────────────────────────────────
DEMO_MODE    = None         # None=auto | True=force demo | False=force live
DISPLAY_MODE = "terminal"   # "terminal" | "tkinter"  (override with --gui flag)
