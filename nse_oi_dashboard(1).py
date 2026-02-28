# ════════════════════════════════════════════════════════════════
#  NSE LIVE OI DASHBOARD  v5.2
#  Works: Google Colab + Local PC (Windows / Mac / Linux)
#
#  ENHANCED with haripm2211/livemarket_option_trading_bot logic:
#    • RollingRSI + RollingVWAP (StrategyEngine from their repo)
#    • RSI/VWAP as 4th bias vote → 4-factor majority vote system
#    • Thread-safe NSE session (cookie_lock pattern from their nse/)
#    • Symbol-aware nearest-strike rounding (nf=50, bnf=100)
#    • _get_highest_oi_strikes() — repo's efficient narrow-window fetch
#    • fetch_oi_data_for_ui() full port: resistance/support/expiry per index
#    • Expiry dropdown in Tkinter GUI (2-step: index → expiry selection)
#
#  v5.2 — ported from nse.py / nse_oi1.py (full source now available):
#    • BUGFIX: score_bias direction was reversed (CE_Chg>PE_Chg = BEARISH)
#    • BUGFIX: resistance/support now uses max ΔOI not max total OI
#    • PCR_THRESHOLDS: exact values from NsePcrSignal class
#    • Localized PCR (ATM ±8 strikes only, per NsePcrSignal.calculate_localized_pcr)
#    • 5-level signal: STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL
#    • Volume data (totalTradedVolume) added to build_df
#    • Volume-weighted net score: Σ(CE_Chg×CE_Vol) - Σ(PE_Chg×PE_Vol)
#    • Chart: ΔOI+volume bar annotations, ATM bar highlight, PCR signal box
#    • NSE_OI_Viewer dual-index panel in Tkinter GUI (daemon thread refresh)
#
#  All previous features:
#    • Stable, non-scrolling display (Colab + terminal)
#    • SignalScorer: rates every signal 0-100 across 8 factors
#    • TradeFilter: max 3 best trades/day, min score gate,
#      min 45-min gap between trades, duplicate-bias guard
#    • All other signals shown as SKIPPED with reason
#    • EOD backtest only on TAKEN trades (no brokerage bleed)
#    • Auto-tuner: PCR thresholds self-adjust every 20 cycles
#    • Demo Mode: auto-activates when markets are closed
# ════════════════════════════════════════════════════════════════
#
#  pip install requests pandas matplotlib yfinance
#  python nse_oi_dashboard.py
#  (Colab: paste full file into one cell and run)
# ════════════════════════════════════════════════════════════════

# ── Auto-install in Colab ────────────────────────────────────────
import sys, os

try:
    import google.colab
    IN_COLAB = True
    os.system("pip install -q yfinance requests pandas matplotlib")
except ImportError:
    IN_COLAB = False

# ── Core imports ─────────────────────────────────────────────────
import math, random, time, threading
import requests
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import yfinance as yf
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict

# ── IPython display (Colab / Jupyter) ───────────────────────────
try:
    from IPython.display import clear_output, display as ipy_display
    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

# ── Matplotlib backend ───────────────────────────────────────────
if IN_COLAB:
    SAVE_PLOT = False                   # Colab renders inline
else:
    matplotlib.use("Agg")              # Headless on local — writes PNG
    SAVE_PLOT = True

# ════════════════════════════════════════════════════════════════
#  CONFIGURATION  — edit only this block
# ════════════════════════════════════════════════════════════════
SYMBOL        = "NIFTY"    # "NIFTY" or "BANKNIFTY"
REFRESH_RATE  = 35         # Seconds between NSE calls (keep >= 35)
MAX_RETRIES   = 3
LOT_SIZE      = 50         # Nifty=50, BankNifty=15
LOG_FILE      = f"{SYMBOL}_OI_{datetime.now().strftime('%Y%m%d')}.csv"
PLOT_FILE     = f"{SYMBOL}_chart.png"

# Tunable thresholds (AutoTuner adjusts these automatically)
PCR_BEARISH   = 1.20       # PCR above this -> bearish
PCR_BULLISH   = 0.80       # PCR below this -> bullish
ROC_THRESHOLD = 50_000     # OI change/cycle to fire RoC alert

# Trade Filter — controls how many trades are taken per day
MAX_TRADES_PER_DAY  = 3    # Hard cap: never more than 3 trades/day
MIN_SIGNAL_SCORE    = 55   # 0-100 score; below this = always skip
MIN_TRADE_GAP_MINS  = 45   # Minimum minutes between two taken trades

# 5-Level PCR Signal thresholds  (from NsePcrSignal class in nse.py)
# Contrarian interpretation: low PCR = heavy call writing = overbought = BUY
# STRONG_BUY ≤ 0.75 < BUY ≤ 0.90 < NEUTRAL < SELL ≥ 1.10 < STRONG_SELL ≥ 1.30
PCR_THRESHOLDS = {
    "STRONG_BUY":  0.75,   # Extreme call writing → deeply oversold → aggressive buy
    "BUY":         0.90,   # Call writing → oversold → buy
    "SELL":        1.10,   # Put writing → overbought → sell
    "STRONG_SELL": 1.30,   # Extreme put writing → deeply overbought → aggressive sell
}
LOCALIZED_PCR_RANGE = 8   # ±8 strikes around ATM for PCR (per NsePcrSignal.STRIKE_RANGE)

# StrategyEngine params (from haripm2211 repo — RollingRSI + RollingVWAP)
RSI_PERIOD    = 14         # Wilder's RSI lookback period
VWAP_WINDOW   = 20         # Rolling VWAP window (cycles, not minutes)
RSI_BULLISH   = 55         # RSI above this + price>VWAP = BULLISH tech signal
RSI_BEARISH   = 35         # RSI below this + price<VWAP = BEARISH tech signal

# Demo Mode: None=auto-detect | True=force demo | False=force live
DEMO_MODE     = None

# Display Mode: "terminal" = Colab/terminal text output (default)
#               "tkinter"  = Live GUI window (local PC only)
#  Run with --gui flag to override:  python nse_oi_dashboard.py --gui
DISPLAY_MODE  = "terminal"
# ════════════════════════════════════════════════════════════════

# ── Tkinter import (only needed for GUI mode) ────────────────────
_TK_AVAILABLE = False
try:
    import tkinter as tk
    import tkinter.ttk as ttk
    _TK_AVAILABLE = True
except ImportError:
    pass  # Tkinter unavailable (Colab / headless) — terminal mode only


# ────────────────────────────────────────────────────────────────
#  SECTION 0 — Market Hours
# ────────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def is_market_open():
    n = now_ist()
    if n.weekday() >= 5:
        return False
    o = n.replace(hour=9,  minute=15, second=0, microsecond=0)
    c = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return o <= n <= c

def is_eod():
    n = now_ist()
    return n.weekday() < 5 and n.hour == 15 and n.minute >= 20

def next_open_str():
    n = now_ist()
    if n.weekday() < 5:
        o = n.replace(hour=9, minute=15, second=0, microsecond=0)
        if n < o:
            mins = int((o - n).seconds // 60)
            return f"Today at 09:15 IST ({mins} min away)"
        skip = 3 if n.weekday() == 4 else 1
    else:
        skip = (7 - n.weekday()) % 7 or 7
    nxt = (n + timedelta(days=skip)).replace(hour=9, minute=15,
                                              second=0, microsecond=0)
    return nxt.strftime("%A %d %b at 09:15 IST")


# ────────────────────────────────────────────────────────────────
#  SECTION 0b — Nearest Strike Helpers  (from haripm2211 repo)
#  Symbol-aware ATM rounding: NIFTY=50pt steps, BANKNIFTY=100pt
# ────────────────────────────────────────────────────────────────
def nearest_strike_nf(x):
    """Round to nearest 50 (Nifty strike spacing)."""
    return int(math.ceil(float(x) / 50) * 50)

def nearest_strike_bnf(x):
    """Round to nearest 100 (BankNifty strike spacing)."""
    return int(math.ceil(float(x) / 100) * 100)

def nearest_strike(x, symbol):
    """Dispatcher — returns symbol-aware ATM strike."""
    return nearest_strike_bnf(x) if symbol == "BANKNIFTY" else nearest_strike_nf(x)

def strike_step(symbol):
    """OTM step size for each symbol."""
    return 100 if symbol == "BANKNIFTY" else 50


# ────────────────────────────────────────────────────────────────
#  SECTION 0c — Technical Indicators  (from haripm2211 repo)
#  Ported from components/indicators.py — RollingRSI + RollingVWAP
# ────────────────────────────────────────────────────────────────
class RollingRSI:
    """
    Wilder's RSI using exponential smoothing.
    Matches haripm2211's RollingRSI — needs `period` candles before
    returning a value; returns None until warmed up.

    Usage:
        rsi = RollingRSI(14)
        val = rsi.update(price)   # val is None until 15+ prices fed
    """
    def __init__(self, period: int = 14):
        self.period   = period
        self.prices   = []
        self.avg_gain = None
        self.avg_loss = None

    def update(self, price: float):
        self.prices.append(price)
        n = len(self.prices)

        if n < self.period + 1:
            return None                                # not enough data yet

        if n == self.period + 1:
            # Seed: simple average of first `period` changes
            changes   = [self.prices[i] - self.prices[i-1]
                         for i in range(1, self.period + 1)]
            self.avg_gain = sum(max(0,  c) for c in changes) / self.period
            self.avg_loss = sum(max(0, -c) for c in changes) / self.period
        else:
            # Wilder's smoothing (same as EMA with α = 1/period)
            chg = price - self.prices[-2]
            self.avg_gain = (self.avg_gain * (self.period - 1) + max(0,  chg)) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + max(0, -chg)) / self.period

        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def reset(self):
        self.prices   = []
        self.avg_gain = None
        self.avg_loss = None


class RollingVWAP:
    """
    Rolling VWAP over a fixed window.
    Matches haripm2211's RollingVWAP — when volume is unavailable
    (option chain doesn't provide it), defaults to 1.0 per bar,
    making this a volume-weighted price over a rolling SMA window.

    Usage:
        vwap = RollingVWAP(20)
        val  = vwap.update(price, volume=None)  # volume optional
    """
    def __init__(self, window: int = 20):
        self.window  = window
        self.prices  = []
        self.volumes = []

    def update(self, price: float, volume: float = None):
        vol = volume if volume is not None else 1.0
        self.prices.append(price)
        self.volumes.append(vol)
        if len(self.prices) > self.window:
            self.prices.pop(0)
            self.volumes.pop(0)
        if len(self.prices) < 2:
            return None
        total_v  = sum(self.volumes)
        total_pv = sum(p * v for p, v in zip(self.prices, self.volumes))
        return round(total_pv / total_v, 2) if total_v > 0 else None

    def reset(self):
        self.prices  = []
        self.volumes = []


class StrategyEngine:
    """
    Ported from haripm2211's components/strategy_engine.py.
    Combines RSI + VWAP into a directional signal.

    Signal rules (matches their on_tick logic exactly):
      RSI >= RSI_BULLISH AND price > VWAP  ->  BULLISH
      RSI <= RSI_BEARISH AND price < VWAP  ->  BEARISH
      otherwise                             ->  NEUTRAL

    Returns (rsi, vwap, signal) on each call to on_tick().
    Both rsi and vwap return None until warmed up.
    """
    def __init__(self):
        self.rsi         = RollingRSI(RSI_PERIOD)
        self.vwap        = RollingVWAP(VWAP_WINDOW)
        self.last_signal = "NEUTRAL"
        self.last_rsi    = None
        self.last_vwap   = None

    def on_tick(self, price: float, volume: float = None):
        rsi  = self.rsi.update(price)
        vwap = self.vwap.update(price, volume)

        self.last_rsi  = rsi
        self.last_vwap = vwap

        if rsi is None or vwap is None:
            return None, None, "NEUTRAL"   # warming up

        trend  = "UP" if price > vwap else "DOWN"
        signal = "NEUTRAL"
        if rsi >= RSI_BULLISH and trend == "UP":
            signal = "BULLISH"
        elif rsi <= RSI_BEARISH and trend == "DOWN":
            signal = "BEARISH"

        self.last_signal = signal
        return rsi, vwap, signal

    def reset(self):
        self.rsi.reset()
        self.vwap.reset()
        self.last_signal = "NEUTRAL"
        self.last_rsi    = None
        self.last_vwap   = None


@dataclass
class TradeRec:
    label:    str       # Conservative / Moderate / Aggressive
    strike:   int
    opt_type: str       # CE or PE
    premium:  float
    sl:       float     # stop-loss premium level
    target:   float     # target premium level
    lot_cost: float     # capital for 1 lot
    rr:       str
    reason:   str

@dataclass
class Signal:
    time:            str
    bias:            str
    spot:            float
    pcr:             float
    rec1:            TradeRec
    rec2:            TradeRec
    rec3:            TradeRec
    score:           int   = 0      # 0-100 quality score
    taken:           bool  = False  # True = one of the 3 best trades taken
    skip_reason:     str   = ""     # Why it was skipped (if taken=False)
    votes_unanimous: bool  = False  # All 4 bias factors agreed
    rsi:             Optional[float] = None   # RSI at signal time
    vwap:            Optional[float] = None   # VWAP at signal time
    tech_signal:     str   = "NEUTRAL"        # RSI+VWAP signal
    spot_exit:       Optional[float] = None
    outcome:         Optional[str]   = None


# ────────────────────────────────────────────────────────────────
#  SECTION 2 — Global State
# ────────────────────────────────────────────────────────────────
prev_oi         = {}   # for RoC detection
signal_log      = []   # all signals (taken + skipped)
accuracy_window = []   # rolling accuracy for auto-tuner

# StrategyEngine singleton — persists across cycles so RSI/VWAP warm up
strategy_engine = StrategyEngine()

# Daily trade tracker (resets at session start)
daily_trades_taken   = 0         # how many trades taken today
last_trade_time      = None      # datetime of last taken trade (IST-aware)


# ────────────────────────────────────────────────────────────────
#  SECTION 3 — OI Analytics
# ────────────────────────────────────────────────────────────────
def calc_max_pain(df):
    best_strike, best_loss = None, float("inf")
    for exp in df["Strike"]:
        loss = (
            (df["CE_OI"] * (exp - df["Strike"]).clip(lower=0)).sum()
            + (df["PE_OI"] * (df["Strike"] - exp).clip(lower=0)).sum()
        )
        if loss < best_loss:
            best_loss, best_strike = loss, exp
    return float(best_strike)

def calc_localized_pcr(df, spot):
    """
    Localized PCR: only counts OI within ±LOCALIZED_PCR_RANGE strikes of ATM.
    Port of NsePcrSignal.calculate_localized_pcr() from nse.py.

    Why: Full-chain PCR is distorted by far OTM strikes that nobody will exercise.
    ATM ±8 strikes capture where the real hedging activity is concentrated.

    Returns float PCR or None if data insufficient.
    """
    strikes = sorted(df["Strike"].tolist())
    if not strikes:
        return None
    # Find ATM index
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    start = max(0, atm_idx - LOCALIZED_PCR_RANGE)
    end   = min(len(strikes), atm_idx + LOCALIZED_PCR_RANGE + 1)
    local_df  = df[df["Strike"].isin(strikes[start:end])]
    total_ce  = local_df["CE_OI"].sum()
    total_pe  = local_df["PE_OI"].sum()
    if total_ce == 0:
        return None
    return round(total_pe / total_ce, 3)


def generate_pcr_signal(pcr):
    """
    5-level contrarian PCR signal.
    Port of NsePcrSignal.generate_signal() and plot._generate_signal() from nse.py.

    Contrarian interpretation:
      Low PCR  (calls >> puts): too many people long via calls → market overbought
                                → smart put buyers wait → contrarian BUY dip signal
      High PCR (puts >> calls): too many people hedging down → market oversold
                                → contrarian SELL rally signal

    Returns (signal_text: str, signal_color: str)
    """
    if pcr is None:
        return "NEUTRAL", "#f39c12"
    if pcr <= PCR_THRESHOLDS["STRONG_BUY"]:
        return "STRONG BUY",  "#006400"   # dark green
    if pcr <= PCR_THRESHOLDS["BUY"]:
        return "BUY",         "#2ecc71"   # green
    if pcr >= PCR_THRESHOLDS["STRONG_SELL"]:
        return "STRONG SELL", "#8b0000"   # dark red
    if pcr >= PCR_THRESHOLDS["SELL"]:
        return "SELL",        "#e74c3c"   # red
    return "NEUTRAL", "#f39c12"           # orange
    global prev_oi
    alerts, new_state = [], {}
    for item in data_items:
        if item.get("expiryDate") != expiry:
            continue
        s  = item["strikePrice"]
        co = item.get("CE", {}).get("openInterest", 0)
        po = item.get("PE", {}).get("openInterest", 0)
        new_state[s] = {"CE": co, "PE": po}
        if s in prev_oi:
            ce_d = co - prev_oi[s]["CE"]
            pe_d = po - prev_oi[s]["PE"]
            if ce_d > ROC_THRESHOLD:
                alerts.append(
                    f"CALL BUILDUP  Strike {int(s):,}  +{ce_d:,} OI/{REFRESH_RATE}s"
                )
            if pe_d > ROC_THRESHOLD:
                alerts.append(
                    f"PUT  BUILDUP  Strike {int(s):,}  +{pe_d:,} OI/{REFRESH_RATE}s"
                )
    prev_oi = new_state
    return alerts

def build_df(data_items, expiry):
    rows = []
    for item in data_items:
        if item.get("expiryDate") != expiry:
            continue
        ce = item.get("CE", {})
        pe = item.get("PE", {})
        rows.append({
            "Strike":  item["strikePrice"],
            "CE_OI":   ce.get("openInterest", 0),
            "PE_OI":   pe.get("openInterest", 0),
            "CE_Chg":  ce.get("changeinOpenInterest", 0),
            "PE_Chg":  pe.get("changeinOpenInterest", 0),
            "CE_LTP":  ce.get("lastPrice", 0),
            "PE_LTP":  pe.get("lastPrice", 0),
            "CE_Vol":  ce.get("totalTradedVolume", 0),   # v5.2: volume for weighted score
            "PE_Vol":  pe.get("totalTradedVolume", 0),   # v5.2: volume for weighted score
        })
    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)


# ────────────────────────────────────────────────────────────────
#  SECTION 3b — Signal Scorer  (0 – 100 points)
# ────────────────────────────────────────────────────────────────
#
#  Factor breakdown — 8 factors, max 100 pts total:
#  ┌──────────────────────────────────────────────────┬──────┐
#  │ Factor                                           │  Max │
#  ├──────────────────────────────────────────────────┼──────┤
#  │ 1. Bias unanimity (all 4 votes agree)            │  20  │
#  │ 2. PCR extremity (distance from 1.0)             │  15  │
#  │ 3. Net OI score magnitude                        │  15  │
#  │ 4. Max Pain alignment (spot moving toward pain)  │  10  │
#  │ 5. VIX in tradable zone (12–18)                  │  10  │
#  │ 6. Time of day (avoid open/close noise)          │  10  │
#  │ 7. RoC confirmation (alert aligns bias)          │   5  │
#  │ 8. RSI+VWAP confirmation (StrategyEngine vote)   │  15  │
#  └──────────────────────────────────────────────────┴──────┘
#  Total: 100.  MIN_SIGNAL_SCORE (default 55) gates entry.
#  Factor 8 sourced from haripm2211 StrategyEngine.on_tick() logic.
#
def score_signal(bias, votes, pcr, net_score, spot, max_pain,
                 vix_str, roc_alerts, tech_signal="NEUTRAL") -> tuple:
    """Returns (score: int, breakdown: dict, unanimous: bool)."""
    pts = {}

    # 1. Bias unanimity — now checks all 4 votes (includes tech_signal)
    unanimous = len(set(votes)) == 1
    pts["unanimity"] = 20 if unanimous else (10 if votes.count(bias) >= 3 else 0)

    # 2. PCR extremity — how far from neutral (1.0)
    pcr_dist = abs(pcr - 1.0)
    pts["pcr"] = min(15, int(pcr_dist * 38))   # 0.40 gap ≈ 15pts

    # 3. Net OI score magnitude (normalised against 1M threshold)
    pts["oi_score"] = min(15, int(abs(net_score) / 300_000 * 15))

    # 4. Max Pain alignment: bonus if bias would carry spot toward max pain
    pain_dist = abs(spot - max_pain)
    moving_toward = (
        (bias == "BULLISH" and spot < max_pain) or
        (bias == "BEARISH" and spot > max_pain)
    )
    pts["max_pain"] = min(10, int(pain_dist / 50) * 2) if moving_toward else 0

    # 5. VIX zone — sweet spot 12-18; too low = no movement, too high = chaos
    try:
        vix = float(vix_str)
        if 12 <= vix <= 18:
            pts["vix"] = 10
        elif 18 < vix <= 22:
            pts["vix"] = 6
        elif vix > 22:
            pts["vix"] = 2   # very risky, widen SL
        else:
            pts["vix"] = 4   # too low, movement is muted
    except (ValueError, TypeError):
        pts["vix"] = 5       # VIX unavailable — neutral

    # 6. Time of day — avoid first 15 min (noise) and last 45 min (gap risk)
    n = now_ist()
    t = n.hour * 60 + n.minute
    OPEN_END    = 9  * 60 + 30    # avoid until 09:30
    CLOSE_START = 14 * 60 + 45   # avoid after 14:45
    if t < OPEN_END or t > CLOSE_START:
        pts["time"] = 0
    elif t <= 10 * 60 + 30:      # 09:30–10:30 — prime setup window
        pts["time"] = 10
    elif t <= 13 * 60:            # 10:30–13:00 — good mid-session
        pts["time"] = 8
    else:
        pts["time"] = 5            # 13:00–14:45 — caution, avoid late entries

    # 7. RoC confirmation — spike in OI that aligns with bias direction
    roc_bonus = 0
    for alert in roc_alerts:
        if bias == "BULLISH" and "CALL" in alert:
            roc_bonus = 5; break
        if bias == "BEARISH" and "PUT" in alert:
            roc_bonus = 5; break
    pts["roc"] = roc_bonus

    # 8. RSI + VWAP confirmation (StrategyEngine from haripm2211 repo)
    #    Full 15pts if tech agrees with OI bias; partial if warming up
    if tech_signal == "NEUTRAL":
        pts["rsi_vwap"] = 5    # warming up / no clear tech edge
    elif tech_signal == bias:
        pts["rsi_vwap"] = 15   # tech and OI agree — strong confirmation
    else:
        pts["rsi_vwap"] = 0    # tech contradicts OI — reduce confidence

    total = min(100, sum(pts.values()))   # cap at 100
    return total, pts, unanimous


# ────────────────────────────────────────────────────────────────
#  SECTION 3c — Trade Filter  (max 3 best trades per day)
# ────────────────────────────────────────────────────────────────
def should_take_trade(score, bias) -> tuple:
    """
    Returns (take: bool, reason: str).
    Rules (checked in order — first failure = skip):
      1. Daily cap not reached (< MAX_TRADES_PER_DAY)
      2. Score >= MIN_SIGNAL_SCORE
      3. Minimum gap since last taken trade (MIN_TRADE_GAP_MINS)
      4. Not same bias as the immediately preceding TAKEN trade
         (avoids doubling down without a reversal signal)
    """
    global daily_trades_taken, last_trade_time

    if daily_trades_taken >= MAX_TRADES_PER_DAY:
        return False, f"Daily cap reached ({MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY})"

    if score < MIN_SIGNAL_SCORE:
        return False, f"Score {score}/100 < min {MIN_SIGNAL_SCORE}"

    if last_trade_time is not None:
        gap_mins = (now_ist() - last_trade_time).seconds // 60
        if gap_mins < MIN_TRADE_GAP_MINS:
            return False, (f"Too soon after last trade "
                           f"({gap_mins}min < {MIN_TRADE_GAP_MINS}min gap)")

    # Duplicate bias guard — don't take same direction back-to-back
    taken_signals = [s for s in signal_log if s.taken]
    if taken_signals and taken_signals[-1].bias == bias:
        return False, f"Same bias ({bias}) as last taken trade — wait for reversal"

    return True, "PASS"


def register_trade_taken():
    """Call this after a trade is approved to update counters."""
    global daily_trades_taken, last_trade_time
    daily_trades_taken += 1
    last_trade_time = now_ist()


# ────────────────────────────────────────────────────────────────
#  SECTION 4 — Strike Recommender
# ────────────────────────────────────────────────────────────────
def recommend_strikes(df, spot, bias, max_pain, resistance, support, symbol="NIFTY"):
    """
    Return exactly 3 trade recommendations.
    Uses symbol-aware strike step (NIFTY=50, BANKNIFTY=100).
    """
    atm  = nearest_strike(spot, symbol)
    step = strike_step(symbol)
    recs = []

    def make_rec(label, strike, opt, reason):
        col  = "CE_LTP" if opt == "CE" else "PE_LTP"
        row  = df[df["Strike"] == float(strike)]
        prem = float(row[col].iloc[0]) if not row.empty else 0
        if prem < 0.5:
            prem = max(2.0, abs(strike - spot) * 0.003 + 8)
        sl     = round(prem * 0.50, 1)
        target = round(prem * 2.00, 1)
        cost   = round(prem * LOT_SIZE, 0)
        return TradeRec(label, int(strike), opt, round(prem, 1),
                        sl, target, cost, "1:2", reason)

    if bias == "BULLISH":
        configs = [
            ("Conservative (ATM)", atm,          "CE",
             f"ATM CE | support wall @ {support:,} | max pain {int(max_pain):,}"),
            ("Moderate (1-OTM)",   atm + step,   "CE",
             f"1 OTM CE | SL if closes below {support:,} | good delta"),
            ("Aggressive (2-OTM)", atm + step*2, "CE",
             f"2 OTM CE | cheap; resistance breakout > {resistance:,}"),
        ]
    elif bias == "BEARISH":
        configs = [
            ("Conservative (ATM)", atm,          "PE",
             f"ATM PE | resistance wall @ {resistance:,} | max pain {int(max_pain):,}"),
            ("Moderate (1-OTM)",   atm - step,   "PE",
             f"1 OTM PE | SL if closes above {resistance:,} | good delta"),
            ("Aggressive (2-OTM)", atm - step*2, "PE",
             f"2 OTM PE | cheap; breakdown expected below {support:,}"),
        ]
    else:  # NEUTRAL
        configs = [
            ("Neutral-ATM CE", atm,        "CE",
             f"Market neutral near {atm:,}; watch for breakout"),
            ("Neutral-ATM PE", atm,        "PE",
             f"Pair with ATM CE for straddle; combined cost = both premiums"),
            ("Hedge OTM CE",   atm + step, "CE",
             f"Upside hedge if market breaks above {atm+step:,}"),
        ]

    for label, strike, opt, reason in configs:
        recs.append(make_rec(label, strike, opt, reason))
    return recs


# ────────────────────────────────────────────────────────────────
#  SECTION 5 — Auto-Tuner
# ────────────────────────────────────────────────────────────────
def auto_tune(sig):
    global PCR_BEARISH, PCR_BULLISH, accuracy_window
    if sig.spot_exit is None:
        return
    correct = (
        (sig.bias == "BULLISH" and sig.spot_exit > sig.spot)
        or (sig.bias == "BEARISH" and sig.spot_exit < sig.spot)
        or sig.bias == "NEUTRAL"
    )
    accuracy_window.append(correct)
    WINDOW = 20
    if len(accuracy_window) < WINDOW:
        return
    accuracy_window = accuracy_window[-WINDOW:]
    acc = sum(accuracy_window) / WINDOW
    if acc < 0.45:
        PCR_BEARISH = round(min(PCR_BEARISH + 0.05, 1.50), 2)
        PCR_BULLISH = round(max(PCR_BULLISH - 0.05, 0.60), 2)
        print(f"  AutoTune: acc={acc:.0%} -> tightened [{PCR_BULLISH}-{PCR_BEARISH}]")
    elif acc > 0.65:
        PCR_BEARISH = round(max(PCR_BEARISH - 0.03, 1.10), 2)
        PCR_BULLISH = round(min(PCR_BULLISH + 0.03, 0.90), 2)
        print(f"  AutoTune: acc={acc:.0%} -> loosened  [{PCR_BULLISH}-{PCR_BEARISH}]")


# ────────────────────────────────────────────────────────────────
#  SECTION 6 — EOD Backtest
# ────────────────────────────────────────────────────────────────
def run_eod_backtest(final_spot):
    taken_sigs   = [s for s in signal_log if s.taken]
    skipped_sigs = [s for s in signal_log if not s.taken]

    W = 72
    print("\n" + "=" * W)
    print("  EOD BACKTEST REPORT  (TAKEN TRADES ONLY)")
    print(f"  Total signals today: {len(signal_log)}")
    print(f"  Taken: {len(taken_sigs)}  |  Skipped: {len(skipped_sigs)}  "
          f"|  Brokerage saved by skipping: ~Rs{len(skipped_sigs) * 3 * 40:,} est.")
    print("=" * W)

    if not taken_sigs:
        print("  No trades were taken today (all signals below quality gate).")
        print("  Tip: Lower MIN_SIGNAL_SCORE or check market conditions.")
        print("=" * W)
        return

    deltas = {
        "Conservative (ATM)": 0.50,
        "Moderate (1-OTM)":   0.35,
        "Aggressive (2-OTM)": 0.20,
    }
    rows, total_pnl = [], 0
    for sig in taken_sigs:
        spot_move = final_spot - sig.spot
        for rec in [sig.rec1, sig.rec2, sig.rec3]:
            delta     = deltas.get(rec.label, 0.35)
            direction = 1 if rec.opt_type == "CE" else -1
            opt_move  = delta * spot_move * direction
            if opt_move <= -(rec.premium - rec.sl):
                pnl_pts = -(rec.premium - rec.sl)
                exit_r  = "SL Hit"
            elif opt_move >= (rec.target - rec.premium):
                pnl_pts = rec.target - rec.premium
                exit_r  = "Target Hit"
            else:
                pnl_pts = opt_move
                exit_r  = "EOD Exit"
            # Approximate brokerage: Rs40 per lot per leg (Zerodha flat + STT)
            brokerage = 40
            pnl_rs    = round(pnl_pts * LOT_SIZE - brokerage, 0)
            total_pnl += pnl_rs
            rows.append({
                "Time":    sig.time,
                "Score":   sig.score,
                "Bias":    sig.bias,
                "Trade":   f"{rec.strike} {rec.opt_type}",
                "Type":    rec.label.split(" ")[0],
                "Entry":   rec.premium,
                "Exit":    exit_r,
                "PnL_Rs":  pnl_rs,
                "Result":  "WIN" if pnl_rs > 0 else "LOSS",
            })

    df   = pd.DataFrame(rows)
    wins = (df["PnL_Rs"] > 0).sum()
    loss = (df["PnL_Rs"] <= 0).sum()

    # Print taken trade detail
    print(f"  Entry spot range: Rs{taken_sigs[0].spot:,.2f} – Rs{taken_sigs[-1].spot:,.2f}")
    print(f"  Final spot: Rs{final_spot:,.2f}")
    print("=" * W)
    hdr = (f"  {'Time':<7} {'Sc':>4} {'Bias':<9} {'Trade':<13} "
           f"{'Type':<16} {'Entry':>7} {'Exit':<14} {'P&L (net)':>10}  Result")
    print(hdr)
    print("  " + "-" * (W - 2))
    for _, r in df.iterrows():
        icon = "+" if r["PnL_Rs"] > 0 else "-"
        print(f"  {r['Time']:<7} {r['Score']:>4} {r['Bias']:<9} {r['Trade']:<13} "
              f"{r['Type']:<16} Rs{r['Entry']:>5.1f}  {r['Exit']:<14} "
              f"Rs{r['PnL_Rs']:>+7,.0f}  {icon}")

    print("=" * W)
    icon = "NET GAIN" if total_pnl > 0 else "NET LOSS"
    print(f"  {icon}: Rs{total_pnl:+,.0f}  (after ~Rs40/lot brokerage per leg)")
    print(f"  Win Rate: {wins/(wins+loss):.0%}  |  Wins: {wins}  |  Losses: {loss}")

    # Skipped signals summary
    if skipped_sigs:
        print("-" * W)
        print(f"  SKIPPED SIGNALS ({len(skipped_sigs)} total — not traded):")
        for s in skipped_sigs:
            print(f"  {s.time}  Score:{s.score:>3}  {s.bias:<9}  {s.skip_reason}")

    print("=" * W)
    print(f"  Note: Delta-approx model. Real fills differ. Brokerage estimated.")
    bt_file = f"{SYMBOL}_backtest_{now_ist().strftime('%Y%m%d')}.csv"
    df.to_csv(bt_file, index=False)
    print(f"  Saved -> {bt_file}")
    print("=" * W)


# ────────────────────────────────────────────────────────────────
#  SECTION 7 — Display Engine (non-scrolling)
# ────────────────────────────────────────────────────────────────
def render(df, symbol, spot, vix, expiry, pcr, bias,
           max_pain, resistance, support, net_score,
           roc_alerts, recs, demo, cycle,
           score=0, score_breakdown=None, taken=False,
           skip_reason="", unanimous=False,
           rsi=None, vwap=None, tech_signal="NEUTRAL",
           pcr_signal="NEUTRAL", pcr_signal_color="#f39c12",
           local_pcr=None):
    """
    Builds entire output as a string list, then clears screen and
    prints everything at once — no mid-render scroll in Colab or terminal.
    Chart is rendered after the text in the same clear_output block.
    In DISPLAY_MODE='tkinter', this is a no-op — OITkApp.update_ui() handles display.
    """
    if DISPLAY_MODE == "tkinter":
        return   # Tkinter app reads Signal object and updates its own widgets
    W   = 68
    SEP = "=" * W
    THN = "-" * W
    mode_tag = "DEMO" if demo else "LIVE"
    bias_icon = {"BULLISH": "[B+]", "BEARISH": "[B-]", "NEUTRAL": "[--]"}.get(bias, "")

    L = []  # output lines
    a = L.append

    a(SEP)
    a(f"  {symbol} OI DASHBOARD [{mode_tag}]  "
      f"{now_ist().strftime('%H:%M:%S IST')}  Cycle #{cycle}")
    a(f"  Spot: Rs{spot:,.2f}  |  VIX: {vix}  |  Expiry: {expiry}  |  Lot: {LOT_SIZE}")
    a(SEP)

    # ── Market summary ───────────────────────────────────────────
    pcr_lbl = ("BEARISH" if pcr > PCR_BEARISH else
               "BULLISH" if pcr < PCR_BULLISH else "NEUTRAL")
    local_pcr_str = f"{local_pcr:.3f}" if local_pcr is not None else "N/A"
    a(f"  PCR(full): {pcr:.3f} ({pcr_lbl})  |  PCR(local±{LOCALIZED_PCR_RANGE}): {local_pcr_str}")
    a(f"  5-Level Signal: [{pcr_signal}]  |  {bias_icon} BIAS: {bias}  |  Wtd OI Score: {net_score:+,}")
    a(f"  Max Pain: Rs{int(max_pain):,}  |  "
      f"Resistance (max ΔCE): Rs{resistance:,}  |  "
      f"Support (max ΔPE): Rs{support:,}")

    # RSI + VWAP line (StrategyEngine from haripm2211 repo)
    rsi_str  = f"{rsi:.1f}" if rsi  is not None else "warming..."
    vwap_str = f"Rs{vwap:.1f}" if vwap is not None else "warming..."
    tech_icon = {"BULLISH": "[B+]", "BEARISH": "[B-]", "NEUTRAL": "[--]"}.get(tech_signal, "")
    warming  = rsi is None
    a(f"  RSI({RSI_PERIOD}): {rsi_str:<10}  VWAP: {vwap_str:<12}  "
      f"Tech Signal: {tech_icon} {tech_signal}"
      f"{'  (needs ' + str(RSI_PERIOD+1) + ' cycles to warm up)' if warming else ''}")
    a(THN)

    # ── Signal Score & Trade Filter status ──────────────────────
    bar_filled  = int(score / 5)   # 20-char bar for 100 pts
    bar_empty   = 20 - bar_filled
    score_bar   = "[" + "#" * bar_filled + "." * bar_empty + "]"
    votes_tag   = "UNANIMOUS" if unanimous else "2/3 MAJORITY"

    if daily_trades_taken >= MAX_TRADES_PER_DAY:
        filter_line = (f"  ** DAILY CAP REACHED ({MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY}) **"
                       f"  Monitoring only — no more entries today")
    elif taken:
        filter_line = (f"  >> TRADE TAKEN  ({daily_trades_taken}/{MAX_TRADES_PER_DAY} today)"
                       f"  Score {score}/100 passed all gates")
    else:
        filter_line = (f"  -- SIGNAL SKIPPED  ({daily_trades_taken}/{MAX_TRADES_PER_DAY} taken today)"
                       f"  Reason: {skip_reason}")

    a(f"  SIGNAL SCORE: {score:>3}/100  {score_bar}  Bias votes: {votes_tag}")
    if score_breakdown:
        bd = score_breakdown
        a(f"  Breakdown -> Unanimity:{bd.get('unanimity',0):>2}  "
          f"PCR:{bd.get('pcr',0):>2}  "
          f"OI:{bd.get('oi_score',0):>2}  "
          f"MaxPain:{bd.get('max_pain',0):>2}  "
          f"VIX:{bd.get('vix',0):>2}  "
          f"Time:{bd.get('time',0):>2}  "
          f"RoC:{bd.get('roc',0):>2}  "
          f"RSI+VWAP:{bd.get('rsi_vwap',0):>2}")
    a(filter_line)
    a(THN)

    # ── Top OI additions ─────────────────────────────────────────
    top_ce = df.nlargest(3, "CE_Chg")[["Strike","CE_Chg","CE_LTP"]]
    top_pe = df.nlargest(3, "PE_Chg")[["Strike","PE_Chg","PE_LTP"]]
    a(f"  {'CALL OI ADDITIONS (fresh short positions)':<40}"
      f"  PUT OI ADDITIONS (fresh long/support)")
    for i in range(3):
        cr, pr = top_ce.iloc[i], top_pe.iloc[i]
        a(f"  {int(cr['Strike']):>7,} CE  +{int(cr['CE_Chg']):>9,}  Rs{cr['CE_LTP']:>6.1f}"
          f"    |    "
          f"{int(pr['Strike']):>7,} PE  +{int(pr['PE_Chg']):>9,}  Rs{pr['PE_LTP']:>6.1f}")
    a(THN)

    # ── Trade recommendations ────────────────────────────────────
    a(f"  TRADE RECOMMENDATIONS  (bias={bias}, "
      f"PCR bands: {PCR_BULLISH} / {PCR_BEARISH})")
    a(f"  {'#':<3} {'Strategy':<24} {'Strike':>7}  "
      f"{'Type':<5} {'Prem':>7}  {'SL':>7}  {'Target':>8}  {'1-Lot':>9}")
    a("  " + "." * (W - 2))
    for i, r in enumerate(recs, 1):
        a(f"  {i}   {r.label:<24} {r.strike:>7,}  "
          f"{r.opt_type:<5} Rs{r.premium:>5.1f}  Rs{r.sl:>5.1f}  "
          f"Rs{r.target:>6.1f}  Rs{r.lot_cost:>7,.0f}")
        a(f"      -> {r.reason}   R:R {r.rr}")
    a(THN)

    # ── RoC alerts ───────────────────────────────────────────────
    if roc_alerts:
        a("  ** RATE-OF-CHANGE ALERTS **")
        for al in roc_alerts:
            a(f"  >> {al}")
        a(THN)

    # ── Cheatsheet ───────────────────────────────────────────────
    a("  CONTEXT")
    a("  Price+ OI+  -> Long Buildup  (bullish)  |  Price- OI+  -> Short Buildup  (bearish)")
    a("  Price+ OI-  -> Short Covering (weak)    |  Price- OI-  -> Long Unwinding (temp dip)")
    try:
        vf = float(vix)
        a(f"  VIX {vix}: {'HIGH - widen SL, reduce qty' if vf > 20 else 'NORMAL - standard sizing'}")
    except ValueError:
        pass
    if demo:
        a(f"  [DEMO] Next market open: {next_open_str()}")
    a(f"  Signals logged today: {len(signal_log)}  |  Log: {LOG_FILE}")
    a(SEP)

    # ── Chart ────────────────────────────────────────────────────
    atm_df = df[(df["Strike"] >= spot * 0.96) &
                (df["Strike"] <= spot * 1.04)].copy()
    if atm_df.empty:
        atm_df = df.copy()

    st   = atm_df["Strike"].values
    bw   = max(10, (st.max() - st.min()) / max(len(st), 1) * 0.38)
    off  = bw / 2
    atm  = nearest_strike(spot, symbol)
    atm_idx_arr = [i for i, s in enumerate(st) if s == atm]
    atm_i = atm_idx_arr[0] if atm_idx_arr else None

    # Weighted direction text (from plot.plot_open_interest_data)
    if net_score > 0:
        wt_dir, wt_clr = "Bearish (CE buildup)", "#e74c3c"
    elif net_score < 0:
        wt_dir, wt_clr = "Bullish (PE buildup)", "#2ecc71"
    else:
        wt_dir, wt_clr = "Neutral", "#f39c12"

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
    fig.patch.set_facecolor("#0d1117")
    for ax in (ax1, ax2):
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="white")
        for label in (ax.xaxis.label, ax.yaxis.label, ax.title):
            label.set_color("white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")

    fig.suptitle(
        f"{symbol} [{mode_tag}]  {now_ist().strftime('%d %b %Y %H:%M IST')}"
        f"  Spot Rs{spot:,.0f}  VIX {vix}  MaxPain Rs{int(max_pain):,}"
        f"  PCR(local) {local_pcr_str}  → {pcr_signal}",
        color="white", fontsize=11, fontweight="bold"
    )

    # Left: OI profile (side by side bars) — with ATM border highlight
    ce_bars = ax1.bar(st - off, atm_df["CE_OI"], width=bw, color="#e74c3c",
                      alpha=0.85, label="Call OI")
    pe_bars = ax1.bar(st + off, atm_df["PE_OI"], width=bw, color="#2ecc71",
                      alpha=0.85, label="Put OI")

    # ATM bar highlight — black border (from plot.plot_open_interest_data)
    if atm_i is not None:
        for bar_set in (ce_bars, pe_bars):
            bar_set[atm_i].set_edgecolor("white")
            bar_set[atm_i].set_linewidth(2.5)

    # ΔOI annotations on each bar (from plot.py bar annotation loop)
    max_oi_val = max(atm_df["CE_OI"].max(), atm_df["PE_OI"].max(), 1)
    for idx, (ce_bar, pe_bar) in enumerate(zip(ce_bars, pe_bars)):
        ce_chg = atm_df["CE_Chg"].iloc[idx]
        pe_chg = atm_df["PE_Chg"].iloc[idx]
        ce_vol = atm_df["CE_Vol"].iloc[idx] if "CE_Vol" in atm_df.columns else 0
        pe_vol = atm_df["PE_Vol"].iloc[idx] if "PE_Vol" in atm_df.columns else 0
        # ΔOI label (green=positive buildup, red=unwinding)
        ce_clr = "#2ecc71" if ce_chg >= 0 else "#e74c3c"
        pe_clr = "#2ecc71" if pe_chg >= 0 else "#e74c3c"
        ax1.text(ce_bar.get_x() + ce_bar.get_width(),
                 ce_bar.get_height() + max_oi_val * 0.02,
                 f"Δ:{ce_chg/1000:+.0f}k", ha="center", va="bottom",
                 fontsize=6.5, color=ce_clr, rotation=90)
        ax1.text(pe_bar.get_x() + pe_bar.get_width(),
                 pe_bar.get_height() + max_oi_val * 0.02,
                 f"Δ:{pe_chg/1000:+.0f}k", ha="center", va="bottom",
                 fontsize=6.5, color=pe_clr, rotation=90)
        # Volume label (purple, like repo)
        if ce_vol > 0:
            ax1.text(ce_bar.get_x() + ce_bar.get_width() / 2,
                     ce_bar.get_height() + max_oi_val * 0.08,
                     f"V:{ce_vol/1000:.0f}k", ha="center", va="bottom",
                     fontsize=5.5, color="#9b59b6", rotation=90)

    ax1.axvline(spot,       color="#3498db", lw=2,   ls="--", label="Spot")
    ax1.axvline(max_pain,   color="#f39c12", lw=1.5, ls=":",  label="MaxPain")
    ax1.axvline(resistance, color="#e74c3c", lw=1,   ls=":",  label=f"Res(ΔCE)")
    ax1.axvline(support,    color="#2ecc71", lw=1,   ls=":",  label=f"Sup(ΔPE)")

    # Market direction text (from plot.py ax0.text Market Direction)
    ax1.text(0.5, 0.94, f"Wtd Direction: {wt_dir}",
             transform=ax1.transAxes, ha="center", va="top",
             fontsize=10, color=wt_clr, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.3", fc="#161b22", alpha=0.8))

    ax1.set_title("Open Interest  (white border=ATM)", color="white")
    ax1.set_xlabel("Strike", color="white")
    ax1.set_ylabel("OI", color="white")
    ax1.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"{x/1e5:.1f}L"))
    ax1.legend(fontsize=7, facecolor="#161b22", labelcolor="white")
    ax1.grid(True, alpha=0.15, color="white")

    # Right: OI change with PCR annotation box (from plot._calculate_localized_pcr)
    cc = ["#e74c3c" if v > 0 else "#555" for v in atm_df["CE_Chg"]]
    pc = ["#2ecc71" if v > 0 else "#555" for v in atm_df["PE_Chg"]]
    ax2.bar(st - off, atm_df["CE_Chg"], width=bw,
            color=cc, alpha=0.85, label="Call ΔOI")
    ax2.bar(st + off, atm_df["PE_Chg"], width=bw,
            color=pc, alpha=0.85, label="Put ΔOI")
    ax2.axhline(0,    color="white", lw=0.6)
    ax2.axvline(spot, color="#3498db", lw=2, ls="--")
    for r in recs:
        ax2.axvline(r.strike, color="#f39c12", lw=1.2,
                    ls="-.", alpha=0.8, label=f"Rec {r.strike}")

    # PCR annotation box (from plot.py pcr_annotation box)
    local_pcr_disp = f"{local_pcr:.3f}" if local_pcr is not None else "N/A"
    pcr_txt = f"Local PCR: {local_pcr_disp}\nSIGNAL: {pcr_signal}"
    ax2.text(0.02, 0.97, pcr_txt,
             transform=ax2.transAxes,
             fontsize=9, fontweight="bold",
             verticalalignment="top",
             color="white",
             bbox=dict(facecolor=pcr_signal_color, alpha=0.75,
                       boxstyle="round,pad=0.4"))

    ax2.set_title("OI Change / Fresh Positions  (orange=recommended strikes)",
                  color="white")
    ax2.set_xlabel("Strike", color="white")
    ax2.set_ylabel("Change in OI", color="white")
    ax2.legend(fontsize=7, facecolor="#161b22", labelcolor="white")
    ax2.grid(True, alpha=0.15, color="white")

    plt.tight_layout()

    # ── Atomic screen update (prevents scroll) ───────────────────
    if HAS_IPYTHON:
        clear_output(wait=True)      # clear ALL previous cell output
        print("\n".join(L))
        ipy_display(fig)             # chart renders in same cell, no scroll
    else:
        os.system("cls" if os.name == "nt" else "clear")
        print("\n".join(L))
        if SAVE_PLOT:
            plt.savefig(PLOT_FILE, dpi=110, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            print(f"  Chart -> {PLOT_FILE}")

    plt.close(fig)


# ────────────────────────────────────────────────────────────────
#  SECTION 8 — NSE Session + Fetchers
#  Thread-safe cookie pattern ported from haripm2211 nse/nse_data.py
#  Uses a global session + cookie_lock to prevent race conditions
#  when session refresh and data fetch happen in overlapping threads.
# ────────────────────────────────────────────────────────────────
NSE_HEADERS = {
    "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/option-chain",
}

_global_session = requests.Session()
_global_cookies = {}
_cookie_lock    = threading.Lock()   # from haripm2211 nse/nse_data.py

def _set_cookie(session, cookies_dict):
    """
    Thread-safe NSE cookie fetch — mirrors haripm2211's set_cookie().
    Holds cookie_lock so concurrent calls don't corrupt the session.
    """
    with _cookie_lock:
        try:
            r = session.get(
                "https://www.nseindia.com/option-chain",
                headers=NSE_HEADERS, timeout=8
            )
            cookies_dict.update(r.cookies)
        except requests.RequestException:
            pass

def create_session():
    """
    3-step warm-up: homepage -> cookie set -> ready.
    Rebuilds global session from scratch to clear stale state.
    """
    global _global_session, _global_cookies
    _global_session = requests.Session()
    _global_session.headers.update(NSE_HEADERS)
    _global_cookies = {}
    try:
        _global_session.get("https://www.nseindia.com/", timeout=10)
        time.sleep(1.2)
        _set_cookie(_global_session, _global_cookies)
        time.sleep(0.8)
        print("NSE session ready (thread-safe cookie mode).")
    except requests.RequestException as e:
        print(f"Session warm-up issue: {e}")
    return _global_session

def _get_data(url):
    """
    Fetch with automatic cookie refresh on 401/403.
    Mirrors haripm2211's get_data() — sets cookie before every call.
    """
    _set_cookie(_global_session, _global_cookies)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = _global_session.get(
                url, headers=NSE_HEADERS,
                cookies=_global_cookies, timeout=10
            )
            if r.status_code == 200:
                return r.text
            if r.status_code in (401, 403):
                print(f"HTTP {r.status_code} — refreshing cookies (attempt {attempt})...")
                _set_cookie(_global_session, _global_cookies)
                time.sleep(3 * attempt)
        except requests.Timeout:
            print(f"Timeout attempt {attempt}")
        except requests.RequestException as e:
            print(f"Network error: {e}")
        time.sleep(4 * attempt)
    return ""

def fetch_chain(session, symbol):
    """Fetch option chain for symbol. Session arg kept for API compatibility."""
    import json
    url  = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    text = _get_data(url)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None

def fetch_vix():
    """Fetch India VIX via yfinance."""
    try:
        v = yf.Ticker("^INDIAVIX").history(period="1d")
        if not v.empty:
            return str(round(v["Close"].iloc[-1], 2))
    except Exception:
        pass
    return "N/A"


# ────────────────────────────────────────────────────────────────
#  SECTION 8b — Dual-Index Fetcher  (v5.1: full fetch_oi_data_for_ui port)
#  _get_highest_oi_strikes() is a direct port of haripm2211's helper:
#    - narrows to ±10 strikes around ATM before scanning max OI
#    - returns resistance (max CE OI strike) + support (max PE OI strike)
#    - returns current expiry date for the combobox
#  fetch_oi_data_dual() is the full fetch_oi_data_for_ui() equivalent:
#    - one allIndices call for both NIFTY and BANKNIFTY spots
#    - then _get_highest_oi_strikes() for each symbol
#    - returns ltp, nearest_strike, oi_data, max_resistance,
#              max_support, expiry keyed by symbol name
# ────────────────────────────────────────────────────────────────
def _get_highest_oi_strikes(num, step, nearest, url):
    """
    Direct port of haripm2211's _get_highest_oi_strikes().
    Fetches option chain and narrows to nearest ± (num * step) strikes
    around ATM. Returns (oi_data_list, max_ce_strike, max_pe_strike, expiry).

    This is more efficient than pulling the full chain because it limits the
    OI table to the ±10 strike window before scanning for max OI — exactly
    how their NSE_OI_UI feeds plot.plot_open_interest_data().
    """
    import json as _j
    text = _get_data(url)
    if not text:
        return [], 0, 0, ""
    try:
        data = _j.loads(text)
        expiry_dates = data["records"].get("expiryDates", [])
        if not expiry_dates:
            return [], 0, 0, ""
        curr_expiry = expiry_dates[0]

        start_strike = nearest - (step * num)
        end_strike   = nearest + (step * num)

        max_oi_ce = 0; max_oi_ce_strike = 0
        max_oi_pe = 0; max_oi_pe_strike = 0
        oi_data_list = []

        for item in data["records"]["data"]:
            if item.get("expiryDate") != curr_expiry:
                continue
            sp = item["strikePrice"]
            if not (start_strike <= sp <= end_strike):
                continue
            ce_oi = item.get("CE", {}).get("openInterest", 0)
            pe_oi = item.get("PE", {}).get("openInterest", 0)
            oi_data_list.append({"strike": sp, "ce_oi": ce_oi, "pe_oi": pe_oi})
            if ce_oi > max_oi_ce:
                max_oi_ce = ce_oi; max_oi_ce_strike = sp
            if pe_oi > max_oi_pe:
                max_oi_pe = pe_oi; max_oi_pe_strike = sp

        oi_data_list.sort(key=lambda x: x["strike"])
        return (oi_data_list,
                int(max_oi_ce_strike), int(max_oi_pe_strike),
                curr_expiry)
    except Exception:
        return [], 0, 0, ""


def fetch_oi_data_dual():
    """
    Full port of haripm2211's fetch_oi_data_for_ui().
    One call to allIndices gets both spots, then _get_highest_oi_strikes()
    pre-computes resistance (max CE OI) and support (max PE OI) for each
    symbol — returned in the same structure as the repo's dict.

    Returns dict keyed by "NIFTY" / "BANKNIFTY", each containing:
        ltp, nearest_strike, oi_data, max_resistance, max_support, expiry
    or None on failure.
    """
    import json as _json
    text = _get_data("https://www.nseindia.com/api/allIndices")
    if not text:
        return None
    try:
        data = _json.loads(text)
        nf_ul = 0; bnf_ul = 0
        for idx in data["data"]:
            if idx["index"] == "NIFTY 50":
                nf_ul = idx["last"]
            if idx["index"] == "NIFTY BANK":
                bnf_ul = idx["last"]
        if nf_ul == 0 and bnf_ul == 0:
            return None

        nf_nearest  = nearest_strike_nf(nf_ul)
        bnf_nearest = nearest_strike_bnf(bnf_ul)

        nf_oi,  nf_res,  nf_sup,  nf_expiry  = _get_highest_oi_strikes(
            10, 50,  nf_nearest,
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY")
        bnf_oi, bnf_res, bnf_sup, bnf_expiry = _get_highest_oi_strikes(
            10, 100, bnf_nearest,
            "https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY")

        return {
            "NIFTY": {
                "ltp":            nf_ul,
                "nearest_strike": nf_nearest,
                "atm":            nf_nearest,        # kept for v5.0 compat
                "oi_data":        nf_oi,
                "max_resistance": nf_res,
                "max_support":    nf_sup,
                "expiry":         nf_expiry,
            },
            "BANKNIFTY": {
                "ltp":            bnf_ul,
                "nearest_strike": bnf_nearest,
                "atm":            bnf_nearest,
                "oi_data":        bnf_oi,
                "max_resistance": bnf_res,
                "max_support":    bnf_sup,
                "expiry":         bnf_expiry,
            },
        }
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────
#  SECTION 8c — Tkinter Live Dashboard  (DISPLAY_MODE = "tkinter")
#  Full GUI port of haripm2211 NSE_OI_UI widget pattern:
#    • Dark theme (#0d1117 background matching their oi.png screenshot)
#    • Notebook tabs: "Signal" tab (main OI) + "Dual OI" tab (NSE_OI_Viewer port)
#    • OI Treeview table with ATM highlight (from their plot.py style)
#    • Signal panel: bias, PCR(local), 5-level signal, Score bar, RSI/VWAP
#    • Trade recommendations panel with row colouring
#    • Scrollable signal log at bottom
#    • Reset/New Day button (from their add_reset_button() pattern)
#    • after() refresh loop — no blocking (their frame.after(5000,...) pattern)
#    • Dual OI tab: NSE_OI_Viewer style (NIFTY+BANKNIFTY side by side)
#      daemon threading + _check_thread_status() from nse_oi1.py
# ────────────────────────────────────────────────────────────────
class OITkApp:
    # ── Colour palette ───────────────────────────────────────────
    C = {
        "bg":      "#0d1117",
        "panel":   "#161b22",
        "border":  "#30363d",
        "sub":     "#21262d",
        "white":   "#e6edf3",
        "muted":   "#8b949e",
        "green":   "#2ecc71",
        "red":     "#e74c3c",
        "yellow":  "#f1c40f",
        "orange":  "#f39c12",
        "blue":    "#3498db",
        "ce_hot":  "#2a1515",   # red-tinted row: heavy call OI
        "pe_hot":  "#152a15",   # green-tinted row: heavy put OI
        "atm_bg":  "#1a2a1a",   # ATM strike highlight
        "taken":   "#0d2a0d",   # recommendation row when trade taken
    }

    def __init__(self, root):
        self.root  = root
        self.root.title(f"NSE OI Dashboard v5.2  [{SYMBOL}]")
        self.root.configure(bg=self.C["bg"])
        self.root.geometry("1440x900")
        self.root.resizable(True, True)

        self._cycle           = 0
        self._use_demo        = (True  if DEMO_MODE is True  else
                                 False if DEMO_MODE is False else
                                 not is_market_open())
        self._session         = None if self._use_demo else create_session()
        self._eod_done        = False
        # Expiry selection (from haripm2211 NSE_OI_UI 2-step workflow)
        self._selected_expiry = None   # None = auto (first expiry)
        self._expiry_list     = []     # populated after first fetch
        # Dual-index secondary spot (from fetch_oi_data_for_ui structure)
        self._bnf_spot        = 0.0   # shown in header when SYMBOL==NIFTY

        self._build()
        # Mirroring haripm2211's frame.after(5000, self.process_api_data) pattern
        self.root.after(800, self._refresh)

    # ── Widget construction ──────────────────────────────────────
    def _lbl(self, parent, text, font_size=10, bold=False, fg=None, **kw):
        fw = "bold" if bold else "normal"
        return tk.Label(parent, text=text,
            font=("Courier", font_size, fw),
            bg=kw.pop("bg", self.C["bg"]),
            fg=fg or self.C["white"], **kw)

    def _build(self):
        C = self.C

        # ── Top header ──────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=C["bg"], pady=5)
        hdr.pack(fill="x", padx=12)

        self._lbl(hdr, f"  {SYMBOL}  OI DASHBOARD  v5.2",
                  14, bold=True).pack(side="left")

        mode_txt = "[DEMO]" if self._use_demo else "[LIVE]"
        mode_clr = C["yellow"] if self._use_demo else C["green"]
        self.lbl_mode = self._lbl(hdr, mode_txt, 13, bold=True, fg=mode_clr)
        self.lbl_mode.pack(side="left", padx=12)

        self.lbl_spot = self._lbl(hdr, "Spot: --", 13, bold=True, fg=C["blue"])
        self.lbl_spot.pack(side="left", padx=18)

        self.lbl_vix = self._lbl(hdr, "VIX: --  |  Lot: --", 11)
        self.lbl_vix.pack(side="left")

        self.lbl_time = self._lbl(hdr, "--:--:--", 11, fg=C["muted"])
        self.lbl_time.pack(side="right", padx=8)

        self.lbl_cycle = self._lbl(hdr, "Cycle #0", 10, fg=C["muted"])
        self.lbl_cycle.pack(side="right", padx=6)

        # ── Secondary index spot (from haripm2211 fetch_oi_data_for_ui) ─
        other = "BANKNIFTY" if SYMBOL == "NIFTY" else "NIFTY"
        self.lbl_other_spot = self._lbl(
            hdr, f"{other}: --", 11, fg=C["muted"])
        self.lbl_other_spot.pack(side="right", padx=14)

        # ── Expiry Combobox (from haripm2211 NSE_OI_UI expiry dropdown) ─
        self._lbl(hdr, "Expiry:", 10, fg=C["muted"]).pack(
            side="right", padx=(8, 2))
        self.cmb_expiry = ttk.Combobox(
            hdr, state="readonly", width=12,
            font=("Courier", 9), values=["auto"])
        self.cmb_expiry.current(0)
        self.cmb_expiry.pack(side="right", padx=4)
        # When user picks an expiry, store it so next cycle uses it
        self.cmb_expiry.bind(
            "<<ComboboxSelected>>",
            lambda _: self._on_expiry_select())

        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")

        # ── Notebook tabs: "Signal" + "Dual OI" ──────────────────
        # NSE_OI_Viewer pattern from nse_oi1.py: two index tables side by side
        nb_style = ttk.Style()
        nb_style.theme_use("clam")
        nb_style.configure("Dark.TNotebook",
            background=C["bg"], borderwidth=0)
        nb_style.configure("Dark.TNotebook.Tab",
            background=C["sub"], foreground=C["muted"],
            font=("Courier", 10, "bold"), padding=[12, 4])
        nb_style.map("Dark.TNotebook.Tab",
            background=[("selected", C["panel"])],
            foreground=[("selected", C["white"])])
        self.notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=8, pady=4)

        # ── Tab 1: Signal (existing main view) ───────────────────
        sig_tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(sig_tab, text="  Signal  ")

        # ── Main body: OI table left, panels right (inside sig_tab)
        body = tk.Frame(sig_tab, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=6)

        # ── LEFT: OI Treeview ────────────────────────────────────
        left = tk.Frame(body, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True)

        self._lbl(left, "OPTION CHAIN  (±4% from ATM)", 9,
                  fg=C["muted"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))

        # Style Treeview
        style = ttk.Style()
        style.theme_use("clam")
        for nm in ("OI.Treeview", "OI.Treeview.Heading"):
            style.configure(nm,
                background=C["panel"], foreground=C["white"],
                fieldbackground=C["panel"],
                font=("Courier", 9))
        style.configure("OI.Treeview", rowheight=23)
        style.configure("OI.Treeview.Heading",
            background=C["sub"], foreground=C["muted"],
            font=("Courier", 9, "bold"))
        style.map("OI.Treeview",
            background=[("selected", "#1f6feb")],
            foreground=[("selected", C["white"])])

        cols = ("CE_OI", "CE_Chg", "Strike", "PE_OI", "PE_Chg")
        self.tree = ttk.Treeview(left, columns=cols,
                                  show="headings", style="OI.Treeview", height=24)
        hdrs = {"CE_OI": "CE OI", "CE_Chg": "CE ΔOI", "Strike": "Strike",
                "PE_OI": "PE OI", "PE_Chg": "PE ΔOI"}
        widths = {"CE_OI": 110, "CE_Chg": 95, "Strike": 90,
                  "PE_OI": 110, "PE_Chg": 95}
        for c in cols:
            self.tree.heading(c, text=hdrs[c])
            self.tree.column(c, width=widths[c], anchor="center")

        self.tree.tag_configure("atm",    background=C["atm_bg"])
        self.tree.tag_configure("ce_hot", background=C["ce_hot"])
        self.tree.tag_configure("pe_hot", background=C["pe_hot"])
        self.tree.tag_configure("normal", background=C["panel"])

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        # ── RIGHT: signal + recs panels ──────────────────────────
        right = tk.Frame(body, bg=C["bg"], width=640)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # ── SIGNAL ANALYSIS panel ────────────────────────────────
        sig_frame = tk.LabelFrame(right,
            text="  SIGNAL ANALYSIS  ",
            font=("Courier", 10, "bold"),
            bg=C["panel"], fg=C["muted"], bd=1, relief="solid",
            labelanchor="nw")
        sig_frame.pack(fill="x", pady=(0, 8))

        def row(parent):
            f = tk.Frame(parent, bg=C["panel"])
            f.pack(fill="x", padx=8, pady=2)
            return f

        # Row 1: Bias + PCR + MaxPain
        r1 = row(sig_frame)
        self._lbl(r1, "BIAS:", 11, bold=True,
                  fg=C["muted"], bg=C["panel"]).pack(side="left")
        self.lbl_bias = self._lbl(r1, "--", 15, bold=True,
                                   fg=C["white"], bg=C["panel"])
        self.lbl_bias.pack(side="left", padx=8)
        self.lbl_pcr = self._lbl(r1, "PCR(loc): --", 11,
                                  fg=C["white"], bg=C["panel"])
        self.lbl_pcr.pack(side="left", padx=16)
        self.lbl_pcr_signal = self._lbl(r1, "NEUTRAL", 11, bold=True,
                                         fg=C["orange"], bg=C["panel"])
        self.lbl_pcr_signal.pack(side="left", padx=6)
        self.lbl_maxpain = self._lbl(r1, "MaxPain: --", 11,
                                      fg=C["orange"], bg=C["panel"])
        self.lbl_maxpain.pack(side="left", padx=10)

        # Row 2: support / resistance
        r2 = row(sig_frame)
        self.lbl_res = self._lbl(r2, "Resistance: --", 10,
                                  fg=C["red"], bg=C["panel"])
        self.lbl_res.pack(side="left")
        self.lbl_sup = self._lbl(r2, "Support: --", 10,
                                  fg=C["green"], bg=C["panel"])
        self.lbl_sup.pack(side="left", padx=20)

        # Row 3: Score bar
        r3 = row(sig_frame)
        self._lbl(r3, "SCORE:", 10, fg=C["muted"],
                  bg=C["panel"]).pack(side="left")
        self.lbl_score = self._lbl(r3, "0/100", 12, bold=True,
                                    fg=C["white"], bg=C["panel"])
        self.lbl_score.pack(side="left", padx=6)
        self.score_cv = tk.Canvas(r3, height=14, width=220,
            bg=C["sub"], highlightthickness=0)
        self.score_cv.pack(side="left", padx=4)
        self._score_rect = self.score_cv.create_rectangle(
            0, 0, 0, 14, fill=C["green"], outline="")
        self.lbl_votes = self._lbl(r3, "-- votes", 9,
                                    fg=C["muted"], bg=C["panel"])
        self.lbl_votes.pack(side="left", padx=6)

        # Row 4: score breakdown
        r4 = row(sig_frame)
        self.lbl_breakdown = self._lbl(r4, "Breakdown: --", 8,
                                        fg=C["muted"], bg=C["panel"])
        self.lbl_breakdown.pack(side="left")

        # Row 5: RSI + VWAP + tech signal
        r5 = row(sig_frame)
        self.lbl_rsi = self._lbl(r5, f"RSI({RSI_PERIOD}): warming…",
                                   10, fg=C["white"], bg=C["panel"])
        self.lbl_rsi.pack(side="left")
        self.lbl_vwap_v = self._lbl(r5, "VWAP: warming…",
                                     10, fg=C["white"], bg=C["panel"])
        self.lbl_vwap_v.pack(side="left", padx=16)
        self.lbl_tech = self._lbl(r5, "Tech: NEUTRAL",
                                   10, bold=True, fg=C["muted"], bg=C["panel"])
        self.lbl_tech.pack(side="left", padx=16)

        # Row 6: filter status (full text)
        r6 = row(sig_frame)
        self.lbl_filter = self._lbl(r6, "Waiting for first signal…",
                                     9, fg=C["muted"], bg=C["panel"],
                                     wraplength=580, justify="left")
        self.lbl_filter.pack(side="left")

        # Row 7: daily counter + reset button
        r7 = row(sig_frame)
        self.lbl_counter = self._lbl(r7, f"Trades today: 0/{MAX_TRADES_PER_DAY}",
                                      10, fg=C["white"], bg=C["panel"])
        self.lbl_counter.pack(side="left")

        # "Go Back / Reset" button — mirrors haripm2211 add_reset_button()
        self.btn_reset = tk.Button(r7,
            text="New Day / Reset", command=self._reset_day,
            bg="salmon", fg="black",
            font=("Courier", 9), relief="flat", padx=6, pady=2)
        self.btn_reset.pack(side="right", padx=4)

        # ── TRADE RECOMMENDATIONS panel ──────────────────────────
        rec_frame = tk.LabelFrame(right,
            text="  TRADE RECOMMENDATIONS  ",
            font=("Courier", 10, "bold"),
            bg=C["panel"], fg=C["muted"], bd=1, relief="solid",
            labelanchor="nw")
        rec_frame.pack(fill="x", pady=(0, 8))

        # Header row
        hdr_rec = tk.Frame(rec_frame, bg=C["sub"])
        hdr_rec.pack(fill="x", padx=4, pady=(4, 0))
        col_spec = [("#", 3), ("Strategy", 17), ("Strike", 8), ("T", 4),
                    ("Prem", 7), ("SL", 7), ("Target", 8), ("1-Lot", 9), ("R:R", 5)]
        for txt, w in col_spec:
            tk.Label(hdr_rec, text=txt, font=("Courier", 9, "bold"),
                width=w, bg=C["sub"], fg=C["muted"],
                anchor="w").pack(side="left")

        self._rec_rows   = []
        self._rec_reason = []
        for i in range(3):
            row_frame = tk.Frame(rec_frame, bg=C["panel"])
            row_frame.pack(fill="x", padx=4, pady=1)
            cells = {}
            for key, default, w in [
                ("num",    str(i+1),  3), ("label", "--", 17),
                ("strike", "--",      8), ("type",  "--",  4),
                ("prem",   "--",      7), ("sl",    "--",  7),
                ("target", "--",      8), ("cost",  "--",  9), ("rr", "1:2", 5),
            ]:
                lbl = tk.Label(row_frame, text=default,
                    font=("Courier", 9), width=w,
                    bg=C["panel"], fg=C["white"], anchor="w")
                lbl.pack(side="left")
                cells[key] = lbl
            self._rec_rows.append(cells)

            reason = tk.Label(rec_frame, text="",
                font=("Courier", 8), bg=C["panel"],
                fg=C["muted"], anchor="w", padx=10)
            reason.pack(fill="x")
            self._rec_reason.append(reason)

        # ── SIGNAL LOG panel ─────────────────────────────────────
        log_frame = tk.LabelFrame(right,
            text="  TODAY'S SIGNAL LOG  ",
            font=("Courier", 9, "bold"),
            bg=C["panel"], fg=C["muted"], bd=1, relief="solid",
            labelanchor="nw")
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, height=6,
            bg=C["panel"], fg=C["white"],
            font=("Courier", 8), state="disabled",
            wrap="none", relief="flat", insertbackground=C["white"])
        xsb = tk.Scrollbar(log_frame, orient="horizontal",
                            command=self.log_text.xview)
        ysb = tk.Scrollbar(log_frame, orient="vertical",
                            command=self.log_text.yview)
        self.log_text.config(xscrollcommand=xsb.set,
                              yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        xsb.pack(fill="x")

        # ── Bottom status bar ────────────────────────────────────
        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")
        sbar = tk.Frame(self.root, bg=C["panel"], pady=3)
        sbar.pack(fill="x", padx=12)
        self.lbl_status = self._lbl(sbar, "Initializing…",
                                     9, fg=C["muted"],
                                     bg=C["panel"], anchor="w")
        self.lbl_status.pack(side="left")
        self.lbl_next = self._lbl(sbar, "", 9,
                                   fg=C["muted"], bg=C["panel"])
        self.lbl_next.pack(side="right")

        # ── Tab 2: Dual OI  (NSE_OI_Viewer port from nse_oi1.py) ─────
        # Shows NIFTY + BANKNIFTY OI tables simultaneously.
        # Uses daemon threading + _check_thread_status() from NSE_OI_Viewer.
        dual_tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(dual_tab, text="  Dual OI  ")
        self._build_dual_oi_tab(dual_tab)

    def _build_dual_oi_tab(self, parent):
        """
        Dual-index OI viewer ported from haripm2211 NSE_OI_Viewer (nse_oi1.py).
        Shows NIFTY and BANKNIFTY side by side.
        Refresh button + 60s auto-refresh via daemon thread.
        """
        C = self.C
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)

        # ── Top controls (Refresh button) ────────────────────────
        top = tk.Frame(parent, bg=C["bg"])
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=6)

        self._lbl(top, "NSE Open Interest Viewer  (NIFTY + BANKNIFTY)",
                  12, bold=True, bg=C["bg"]).pack(side="left")
        self._dual_refresh_btn = tk.Button(
            top, text="Refresh OI",
            command=lambda: self._dual_fetch_threaded(manual=True),
            bg=C["sub"], fg=C["white"],
            font=("Courier", 9), relief="flat", padx=8, pady=3)
        self._dual_refresh_btn.pack(side="right", padx=10)

        self._dual_status = self._lbl(
            top, "Fetching…", 9, fg=C["muted"], bg=C["bg"])
        self._dual_status.pack(side="right", padx=6)

        # ── NIFTY column ─────────────────────────────────────────
        nf_col = tk.Frame(parent, bg=C["panel"], bd=1, relief="solid")
        nf_col.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=5)
        nf_col.columnconfigure(0, weight=1)
        nf_col.rowconfigure(2, weight=1)

        self._dual_nf_hdr = self._lbl(
            nf_col, "Nifty 50  |  LTP: --  |  Nearest: --",
            10, bold=True, fg=C["blue"], bg=C["panel"])
        self._dual_nf_hdr.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        nf_sr = tk.Frame(nf_col, bg=C["panel"])
        nf_sr.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._dual_nf_sup = self._lbl(
            nf_sr, "Support: --", 9, fg=C["green"], bg=C["panel"])
        self._dual_nf_sup.pack(side="left")
        self._dual_nf_res = self._lbl(
            nf_sr, "Resistance: --", 9, fg=C["red"], bg=C["panel"])
        self._dual_nf_res.pack(side="left", padx=20)

        self._dual_nf_tree = self._make_dual_tree(nf_col)
        self._dual_nf_tree.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        # ── BANKNIFTY column ─────────────────────────────────────
        bnf_col = tk.Frame(parent, bg=C["panel"], bd=1, relief="solid")
        bnf_col.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=5)
        bnf_col.columnconfigure(0, weight=1)
        bnf_col.rowconfigure(2, weight=1)

        self._dual_bnf_hdr = self._lbl(
            bnf_col, "Bank Nifty  |  LTP: --  |  Nearest: --",
            10, bold=True, fg=C["orange"], bg=C["panel"])
        self._dual_bnf_hdr.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        bnf_sr = tk.Frame(bnf_col, bg=C["panel"])
        bnf_sr.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._dual_bnf_sup = self._lbl(
            bnf_sr, "Support: --", 9, fg=C["green"], bg=C["panel"])
        self._dual_bnf_sup.pack(side="left")
        self._dual_bnf_res = self._lbl(
            bnf_sr, "Resistance: --", 9, fg=C["red"], bg=C["panel"])
        self._dual_bnf_res.pack(side="left", padx=20)

        self._dual_bnf_tree = self._make_dual_tree(bnf_col)
        self._dual_bnf_tree.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        # Start first fetch (60s auto-refresh from NSE_OI_Viewer pattern)
        self._dual_refresh_interval = 60_000   # ms
        parent.after(1200, lambda: self._dual_fetch_threaded())

    def _make_dual_tree(self, parent):
        """
        Create CE OI | Strike | PE OI treeview.
        Matches NSE_OI_Viewer._create_oi_tree() column layout.
        """
        C = self.C
        cols = ("CE OI", "Strike", "PE OI")
        tree = ttk.Treeview(parent, columns=cols,
                             show="headings", style="OI.Treeview", height=16)
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=90, anchor="center")
        tree.tag_configure("atm",    background=C["atm_bg"])
        tree.tag_configure("ce_hot", background=C["ce_hot"])
        tree.tag_configure("pe_hot", background=C["pe_hot"])
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")  # pack handled by caller via grid
        tree.pack(fill="both", expand=True)
        return tree

    def _dual_fetch_threaded(self, manual=False):
        """
        Start background fetch for dual-index OI.
        Uses daemon thread + _check_thread_status() pattern from NSE_OI_Viewer.
        """
        if manual:
            self._dual_refresh_btn.config(state="disabled", text="Fetching…")
            self._dual_status.config(text="Fetching…", fg=self.C["yellow"])

        import threading as _thr
        t = _thr.Thread(target=self._dual_fetch_worker, daemon=True)
        t.start()
        # Poll thread status (from NSE_OI_Viewer._check_thread_status)
        self.root.after(100, lambda: self._dual_check_thread(t, manual))

    def _dual_check_thread(self, thread, manual):
        """
        Mirrors NSE_OI_Viewer._check_thread_status() — polls thread every 100ms.
        Re-enables button and schedules next auto-refresh when done.
        """
        if thread.is_alive():
            self.root.after(100, lambda: self._dual_check_thread(thread, manual))
        else:
            if manual:
                self._dual_refresh_btn.config(state="normal", text="Refresh OI")
            # Schedule next auto-refresh (60s, from NSE_OI_Viewer refresh_interval_ms)
            if not manual:
                self.root.after(
                    self._dual_refresh_interval,
                    lambda: self._dual_fetch_threaded())

    def _dual_fetch_worker(self):
        """
        Background thread: fetch both indices and push update to main thread.
        Mirrors NSE_OI_Viewer._run_nse_oi_script_logic() using fetch_oi_data_dual().
        """
        try:
            oi = fetch_oi_data_dual()
            if oi is None:
                self.root.after(0, lambda: self._dual_status.config(
                    text="Fetch failed ❌", fg=self.C["red"]))
                return
            # All UI updates must happen in main thread (after(0, ...))
            nf  = oi["NIFTY"]
            bnf = oi["BANKNIFTY"]
            self.root.after(0, lambda: self._dual_update_ui(nf, bnf))
        except Exception as e:
            self.root.after(0, lambda: self._dual_status.config(
                text=f"Error: {e}", fg=self.C["red"]))

    def _dual_update_ui(self, nf, bnf):
        """
        Update dual-index panels (main thread).
        Mirrors NSE_OI_Viewer._update_ui_elements() column-by-column update.
        """
        # NIFTY
        self._dual_nf_hdr.config(
            text=(f"Nifty 50  ({nf['expiry']})  |  "
                  f"LTP: {nf['ltp']:,.2f}  |  Nearest: {nf['nearest_strike']:,}"))
        self._dual_nf_sup.config(
            text=f"Support (max ΔPE): {nf['max_support']:,}")
        self._dual_nf_res.config(
            text=f"Resistance (max ΔCE): {nf['max_resistance']:,}")
        self._populate_dual_tree(self._dual_nf_tree, nf["oi_data"],
                                  nf["nearest_strike"])

        # BANKNIFTY
        self._dual_bnf_hdr.config(
            text=(f"Bank Nifty  ({bnf['expiry']})  |  "
                  f"LTP: {bnf['ltp']:,.2f}  |  Nearest: {bnf['nearest_strike']:,}"))
        self._dual_bnf_sup.config(
            text=f"Support (max ΔPE): {bnf['max_support']:,}")
        self._dual_bnf_res.config(
            text=f"Resistance (max ΔCE): {bnf['max_resistance']:,}")
        self._populate_dual_tree(self._dual_bnf_tree, bnf["oi_data"],
                                  bnf["nearest_strike"])

        self._dual_status.config(
            text=f"Updated {now_ist().strftime('%H:%M:%S')}",
            fg=self.C["muted"])

    def _populate_dual_tree(self, tree, data_list, nearest_strike):
        """
        Fill treeview with OI data.
        Mirrors NSE_OI_Viewer._update_oi_tree() with ATM + wall highlighting.
        """
        C = self.C
        tree.delete(*tree.get_children())
        if not data_list:
            return
        max_ce = max((d["ce_oi"] for d in data_list), default=1)
        max_pe = max((d["pe_oi"] for d in data_list), default=1)
        for item in data_list:
            s = item["strike"]
            if s == nearest_strike:
                tag = "atm"
            elif item["ce_oi"] >= max_ce * 0.80:
                tag = "ce_hot"
            elif item["pe_oi"] >= max_pe * 0.80:
                tag = "pe_hot"
            else:
                tag = "normal"
            tree.insert("", "end", tags=(tag,), values=(
                f"{item['ce_oi']:,}",
                f"{int(s):,}" + (" ATM" if s == nearest_strike else ""),
                f"{item['pe_oi']:,}",
            ))

    def _on_expiry_select(self):
        """
        Called when user picks from the expiry combobox.
        Mirrors haripm2211 NSE_OI_UI.plot_api_data() workflow:
        after selecting expiry, the next cycle will use it.
        """
        val = self.cmb_expiry.get()
        self._selected_expiry = None if val == "auto" else val
        self.lbl_status.config(
            text=f"Expiry changed to: {val} — takes effect next cycle",
            fg=self.C["yellow"])

    # ── Refresh cycle ────────────────────────────────────────────
    def _refresh(self):
        """
        One fetch + process cycle.
        Mirrors haripm2211's frame.after() pattern — non-blocking UI loop.
        Now also fetches dual-index spots and populates the expiry combobox
        (from haripm2211 NSE_OI_UI 2-step index→expiry workflow).
        """
        self._cycle += 1
        C = self.C
        self.lbl_cycle.config(text=f"Cycle #{self._cycle}")
        self.lbl_time.config(text=now_ist().strftime("%H:%M:%S IST"))
        self.lbl_status.config(text="Fetching data…", fg=C["yellow"])
        self.root.update_idletasks()

        try:
            if self._use_demo:
                vix  = str(round(14.5 + math.sin(self._cycle * 0.5) * 2.5
                                 + random.uniform(-0.3, 0.3), 2))
                data = demo_data(SYMBOL, self._cycle)
                # Update secondary spot from demo data (BNF ≈ 6× NIFTY heuristic)
                other = "BANKNIFTY" if SYMBOL == "NIFTY" else "NIFTY"
                demo_spot = data["records"]["underlyingValue"]
                self._bnf_spot = demo_spot * 2.1 if SYMBOL == "NIFTY" else demo_spot / 2.1
            else:
                vix  = fetch_vix()
                data = fetch_chain(self._session, SYMBOL)
                if data is None:
                    self.lbl_status.config(
                        text="NSE fetch failed — will retry", fg=C["red"])
                    if not is_market_open():
                        self._use_demo = True
                        self.lbl_mode.config(text="[DEMO]", fg=C["yellow"])
                    self._schedule()
                    return
                if self._cycle % 10 == 0:
                    self._session = create_session()

                # Fetch dual-index spots (from fetch_oi_data_for_ui pattern)
                # Run in background so it doesn't block the main cycle
                try:
                    dual = fetch_oi_data_dual()
                    if dual:
                        other = "BANKNIFTY" if SYMBOL == "NIFTY" else "NIFTY"
                        self._bnf_spot = dual[other]["ltp"]
                except Exception:
                    pass

            # ── Populate expiry combobox (first cycle or on refresh) ──────
            expiry_dates = data["records"].get("expiryDates", [])
            if expiry_dates and expiry_dates != self._expiry_list:
                self._expiry_list = expiry_dates
                self.cmb_expiry.config(values=["auto"] + expiry_dates)
                # If user hasn't manually selected, keep "auto" selected
                if self._selected_expiry is None:
                    self.cmb_expiry.current(0)

            sig = process_cycle(data, SYMBOL, vix, self._use_demo, self._cycle,
                                selected_expiry=self._selected_expiry)

            if sig:
                signal_log.append(sig)
                if len(signal_log) >= 2:
                    prev = signal_log[-2]
                    prev.spot_exit = sig.spot
                    prev.outcome   = (
                        "WIN" if (prev.bias == "BULLISH" and sig.spot > prev.spot)
                             or  (prev.bias == "BEARISH" and sig.spot < prev.spot)
                        else "LOSS"
                    )
                    auto_tune(prev)
                self._update_ui(data, sig, vix)

            # EOD backtest
            if is_eod() and not self._eod_done and not self._use_demo:
                self._eod_done = True
                fs = data["records"]["underlyingValue"]
                for s in signal_log:
                    if s.spot_exit is None:
                        s.spot_exit = fs
                run_eod_backtest(fs)

            self.lbl_status.config(
                text=(f"Updated {now_ist().strftime('%H:%M:%S')}  "
                      f"| Signals: {len(signal_log)}  "
                      f"| Taken: {daily_trades_taken}/{MAX_TRADES_PER_DAY}  "
                      f"| PCR bands: {PCR_BULLISH}/{PCR_BEARISH}"),
                fg=C["muted"])
        except Exception as e:
            self.lbl_status.config(text=f"Error: {e}", fg=C["red"])

        self._schedule()

    def _schedule(self):
        ms = 5_000 if self._use_demo else REFRESH_RATE * 1_000
        self.root.after(ms, self._refresh)

    # ── UI data update ───────────────────────────────────────────
    def _update_ui(self, data, sig, vix):
        """Update all panels with latest signal. Called after process_cycle()."""
        C   = self.C
        rec = data["records"]
        spot   = rec["underlyingValue"]
        # Use selected expiry if user chose one; otherwise first available
        expiry = (self._selected_expiry
                  if self._selected_expiry and self._selected_expiry in rec.get("expiryDates", [])
                  else rec["expiryDates"][0])

        # ── Header ───────────────────────────────────────────────
        self.lbl_spot.config(text=f"Spot: Rs{spot:,.0f}")
        self.lbl_vix.config(
            text=f"VIX: {vix}  |  Expiry: {expiry}  |  Lot: {LOT_SIZE}")

        # Secondary index spot (from fetch_oi_data_for_ui dual structure)
        other = "BANKNIFTY" if SYMBOL == "NIFTY" else "NIFTY"
        if self._bnf_spot > 0:
            self.lbl_other_spot.config(
                text=f"{other}: Rs{self._bnf_spot:,.0f}", fg=C["muted"])

        # ── Build DF (needed for table + derived values) ──────────
        df = build_df(rec["data"], expiry)
        if df.empty:
            return
        max_pain   = calc_max_pain(df)
        # v5.2: resistance/support from max ΔOI (fresh money flow)
        resistance = int(df.loc[df["CE_Chg"].idxmax(), "Strike"])
        support    = int(df.loc[df["PE_Chg"].idxmax(), "Strike"])
        pcr        = round(df["PE_OI"].sum() / max(1, df["CE_OI"].sum()), 3)

        # ── Signal panel ─────────────────────────────────────────
        bias_clr = (C["green"] if sig.bias == "BULLISH" else
                    C["red"]   if sig.bias == "BEARISH" else C["yellow"])
        self.lbl_bias.config(text=sig.bias, fg=bias_clr)
        local_pcr_v = calc_localized_pcr(df, spot)
        local_pcr_str = f"{local_pcr_v:.3f}" if local_pcr_v else "--"
        self.lbl_pcr.config(text=f"PCR(loc±{LOCALIZED_PCR_RANGE}): {local_pcr_str}")
        # 5-level signal display
        sig5_txt, sig5_clr = generate_pcr_signal(local_pcr_v)
        self.lbl_pcr_signal.config(text=sig5_txt, fg=sig5_clr)
        self.lbl_maxpain.config(text=f"MaxPain: Rs{int(max_pain):,}")
        self.lbl_res.config(text=f"Res(ΔCE): Rs{resistance:,}")
        self.lbl_sup.config(text=f"Sup(ΔPE): Rs{support:,}")

        # Score bar
        self.lbl_score.config(text=f"{sig.score}/100")
        bar_w     = int(sig.score * 2.2)   # 100pts → 220px
        score_clr = (C["green"]  if sig.score >= 65 else
                     C["yellow"] if sig.score >= 45 else C["red"])
        self.score_cv.coords(self._score_rect, 0, 0, bar_w, 14)
        self.score_cv.itemconfig(self._score_rect, fill=score_clr)

        votes_tag = "UNANIMOUS" if sig.votes_unanimous else "majority"
        self.lbl_votes.config(text=votes_tag)

        # Score breakdown
        # (sig has no breakdown stored — recompute from process_cycle values)
        rsi_pts  = 0  # approximate display
        self.lbl_breakdown.config(text=f"Score: {sig.score}/100  |  Bias votes: {votes_tag}")

        # RSI / VWAP / tech
        rsi_str  = f"{sig.rsi:.1f}" if sig.rsi  is not None else "warming…"
        vwap_str = f"Rs{sig.vwap:.1f}" if sig.vwap is not None else "warming…"
        tech_clr = (C["green"] if sig.tech_signal == "BULLISH" else
                    C["red"]   if sig.tech_signal == "BEARISH" else C["muted"])
        self.lbl_rsi.config(text=f"RSI({RSI_PERIOD}): {rsi_str}")
        self.lbl_vwap_v.config(text=f"VWAP: {vwap_str}")
        self.lbl_tech.config(text=f"Tech: {sig.tech_signal}", fg=tech_clr)

        # Filter status
        if sig.taken:
            self.lbl_filter.config(
                text=(f">> TRADE TAKEN  ({daily_trades_taken}/{MAX_TRADES_PER_DAY} today)"
                      f"  Score {sig.score}/100 — all gates passed"),
                fg=C["green"])
        else:
            self.lbl_filter.config(
                text=(f"-- SKIPPED  ({daily_trades_taken}/{MAX_TRADES_PER_DAY} taken today)"
                      f"  Reason: {sig.skip_reason}"),
                fg=C["muted"])

        ctr_clr = C["green"] if daily_trades_taken < MAX_TRADES_PER_DAY else C["red"]
        self.lbl_counter.config(
            text=f"Trades today: {daily_trades_taken}/{MAX_TRADES_PER_DAY}",
            fg=ctr_clr)

        # ── Recommendations ──────────────────────────────────────
        for i, rec_item in enumerate([sig.rec1, sig.rec2, sig.rec3]):
            cells  = self._rec_rows[i]
            reason = self._rec_reason[i]
            taken_row = sig.taken
            row_bg = C["taken"] if taken_row else C["panel"]
            type_clr = C["green"] if rec_item.opt_type == "CE" else C["red"]
            label_short = rec_item.label.split("(")[0].strip()[:16]

            for lbl in cells.values():
                lbl.config(bg=row_bg)
            cells["label"].config(text=label_short)
            cells["strike"].config(text=f"{rec_item.strike:,}")
            cells["type"].config(text=rec_item.opt_type, fg=type_clr)
            cells["prem"].config(text=f"Rs{rec_item.premium:.0f}")
            cells["sl"].config(text=f"Rs{rec_item.sl:.0f}")
            cells["target"].config(text=f"Rs{rec_item.target:.0f}")
            cells["cost"].config(text=f"Rs{int(rec_item.lot_cost):,}")
            reason.config(
                text=f"   → {rec_item.reason}",
                fg=C["green"] if taken_row else C["muted"],
                bg=row_bg)

        # ── OI Table ─────────────────────────────────────────────
        for item in self.tree.get_children():
            self.tree.delete(item)

        atm = nearest_strike(spot, SYMBOL)
        window = df[abs(df["Strike"] - spot) <= (spot * 0.04)].copy()
        max_ce = window["CE_OI"].max()
        max_pe = window["PE_OI"].max()

        for _, r in window.iterrows():
            s = int(r["Strike"])
            if s == atm:
                tag = "atm"
            elif r["CE_OI"] >= max_ce * 0.80:
                tag = "ce_hot"
            elif r["PE_OI"] >= max_pe * 0.80:
                tag = "pe_hot"
            else:
                tag = "normal"

            atm_marker = " ATM" if s == atm else (
                         " RES" if s == resistance else (
                         " SUP" if s == support    else ""))
            self.tree.insert("", "end", tags=(tag,), values=(
                f"{int(r['CE_OI']):,}",
                f"{int(r['CE_Chg']):+,}",
                f"{s:,}{atm_marker}",
                f"{int(r['PE_OI']):,}",
                f"{int(r['PE_Chg']):+,}",
            ))

        # ── Signal log append ────────────────────────────────────
        rsi_disp  = f"{sig.rsi:.1f}"  if sig.rsi  is not None else "N/A"
        vwap_disp = f"{sig.vwap:.0f}" if sig.vwap is not None else "N/A"
        entry = (
            f"  {sig.time}  {sig.bias:<9} Sc:{sig.score:>3}"
            f"  {'TAKEN ' if sig.taken else 'skip  '}"
            f"  RSI:{rsi_disp:<7}  Tech:{sig.tech_signal:<9}"
            f"  {sig.rec1.strike}{sig.rec1.opt_type}"
            f"  {sig.skip_reason if not sig.taken else 'ALL GATES PASSED'}\n"
        )
        self.log_text.config(state="normal")
        self.log_text.insert("end", entry)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # ── Reset (haripm2211 reset_ui pattern) ──────────────────────
    def _reset_day(self):
        """
        New Day / Reset — clears daily counters and signal log.
        Mirrors haripm2211 NSE_OI_UI.reset_ui() philosophy:
        wipe state, re-init indicators, start fresh.
        """
        global daily_trades_taken, last_trade_time, signal_log, accuracy_window
        daily_trades_taken = 0
        last_trade_time    = None
        signal_log         = []
        accuracy_window    = []
        strategy_engine.reset()

        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end",
            f"  --- NEW DAY RESET  {now_ist().strftime('%d-%b %H:%M')} ---\n")
        self.log_text.config(state="disabled")

        self.lbl_filter.config(
            text="Day reset — waiting for first signal…",
            fg=self.C["muted"])
        self.lbl_counter.config(
            text=f"Trades today: 0/{MAX_TRADES_PER_DAY}",
            fg=self.C["green"])


def demo_data(symbol, cycle):
    random.seed(cycle)
    spot   = 22_450 + math.sin(cycle * 0.3) * 90 + random.uniform(-20, 20)
    atm    = round(spot / 50) * 50
    expiry = "06-Mar-2026"
    items  = []
    for i in range(21):
        dist = i - 10
        s    = atm + dist * 50
        ce_b = max(1000, int(800_000 * math.exp(-0.08 * (dist + 2) ** 2)))
        pe_b = max(1000, int(900_000 * math.exp(-0.08 * (dist - 2) ** 2)))
        ce_oi  = ce_b + random.randint(-5000, 25000) * (cycle % 3 + 1)
        pe_oi  = pe_b + random.randint(-5000, 25000) * (cycle % 3 + 1)
        ce_chg = max(0, random.randint(500, 35000) + (15000 if dist > 1 else 0))
        pe_chg = max(0, random.randint(500, 35000) + (15000 if dist < -1 else 0))
        if cycle % 4 == 0 and dist == 3:   # RoC spike test
            ce_oi += 130_000; ce_chg += 130_000
        ce_ltp = max(1.0, round((200 - max(0, s - atm)) * 0.9
                                + random.uniform(-3, 3), 1))
        pe_ltp = max(1.0, round((200 - max(0, atm - s)) * 0.9
                                + random.uniform(-3, 3), 1))
        items.append({
            "strikePrice": float(s), "expiryDate": expiry,
            "CE": {"openInterest": ce_oi, "changeinOpenInterest": ce_chg,
                   "lastPrice": ce_ltp},
            "PE": {"openInterest": pe_oi, "changeinOpenInterest": pe_chg,
                   "lastPrice": pe_ltp},
        })
    return {"records": {
        "underlyingValue": round(spot, 2),
        "timestamp":       now_ist().strftime("%d-%b-%Y %H:%M:%S"),
        "expiryDates":     [expiry, "13-Mar-2026", "27-Mar-2026"],
        "data":            items,
    }}


# ────────────────────────────────────────────────────────────────
#  SECTION 10 — Core Processing Pipeline
# ────────────────────────────────────────────────────────────────
def process_cycle(data, symbol, vix, demo, cycle, selected_expiry=None):
    rec    = data["records"]
    spot   = rec["underlyingValue"]
    # Use user-selected expiry if provided; default to first available
    avail_expiries = rec.get("expiryDates", [])
    expiry = (selected_expiry
              if selected_expiry and selected_expiry in avail_expiries
              else avail_expiries[0] if avail_expiries else "")

    df = build_df(rec["data"], expiry)
    if df.empty:
        return None

    total_ce = df["CE_OI"].sum()
    total_pe = df["PE_OI"].sum()
    if total_ce == 0:
        return None

    pcr        = round(total_pe / total_ce, 3)
    # v5.2: localized PCR (ATM ±8 strikes) from NsePcrSignal.calculate_localized_pcr()
    local_pcr  = calc_localized_pcr(df, spot)
    pcr_for_bias = local_pcr if local_pcr is not None else pcr
    pcr_signal, pcr_signal_color = generate_pcr_signal(pcr_for_bias)

    # v5.2 BUGFIX: resistance = max ΔCE (fresh call writing = overhead resistance)
    #              support    = max ΔPE (fresh put writing = floor support)
    # Previously used max total OI, which includes stale positions from prior days.
    # Repo's plot.py uses max ce_change / max pe_change — fresh money only.
    resistance = int(df.loc[df["CE_Chg"].idxmax(), "Strike"])
    support    = int(df.loc[df["PE_Chg"].idxmax(), "Strike"])

    # v5.2: volume-weighted net score from plot.plot_open_interest_data()
    # Σ(CE_Chg × CE_Vol) - Σ(PE_Chg × PE_Vol)
    # >0 = more volume-weighted call buildup = BEARISH (overhead resistance building)
    # <0 = more volume-weighted put buildup  = BULLISH (floor support building)
    weighted_net_score = int(
        (df["CE_Chg"] * df["CE_Vol"]).sum()
        - (df["PE_Chg"] * df["PE_Vol"]).sum()
    )
    raw_net_score = int(df["CE_Chg"].sum() - df["PE_Chg"].sum())
    max_pain   = calc_max_pain(df)   # ← was accidentally dropped in v5.2 refactor

    roc_alerts = compute_roc_alerts(rec["data"], expiry)

    # ── OI-based bias votes (3 factors) ─────────────────────────
    pcr_bias   = ("BEARISH" if pcr_for_bias > PCR_BEARISH else
                  "BULLISH" if pcr_for_bias < PCR_BULLISH else "NEUTRAL")
    # v5.2 BUGFIX: CE_Chg > PE_Chg = more calls added = BEARISH (resistance building)
    # Prior versions had this backwards (was marking CE buildup as BULLISH)
    score_bias = ("BEARISH" if weighted_net_score > 0 else
                  "BULLISH" if weighted_net_score < 0 else "NEUTRAL")
    pain_bias  = ("BULLISH" if spot < max_pain else
                  "BEARISH" if spot > max_pain else "NEUTRAL")

    # ── Technical bias (4th vote — StrategyEngine from haripm2211) ──
    rsi_val, vwap_val, tech_signal = strategy_engine.on_tick(spot)

    # 4-factor majority vote: PCR + OI Score + Max Pain + RSI/VWAP
    votes = [pcr_bias, score_bias, pain_bias, tech_signal]
    bias  = max(set(votes), key=votes.count)

    recs = recommend_strikes(df, spot, bias, max_pain, resistance, support, symbol)
    if len(recs) < 3:
        return None

    # ── Score this signal (8 factors) ───────────────────────────
    score, breakdown, unanimous = score_signal(
        bias, votes, pcr_for_bias, weighted_net_score, spot, max_pain,
        vix, roc_alerts, tech_signal
    )

    # ── Apply trade filter ───────────────────────────────────────
    take, filter_reason = should_take_trade(score, bias)
    if take:
        register_trade_taken()

    # CSV logging (all signals logged, taken column shows filter result)
    log_row = pd.DataFrame([{
        "Timestamp":    rec["timestamp"], "Spot": spot, "VIX": vix,
        "PCR_Full": pcr, "PCR_Local": local_pcr, "PCR_Signal": pcr_signal,
        "MaxPain": max_pain,
        "Resistance":   resistance, "Support": support,
        "WeightedScore": weighted_net_score, "RawOIScore": raw_net_score,
        "Bias": bias,
        "RSI":          round(rsi_val, 2) if rsi_val else "warming",
        "VWAP":         round(vwap_val, 2) if vwap_val else "warming",
        "TechSignal":   tech_signal,
        "Score":        score, "Unanimous": unanimous,
        "Taken":        take, "SkipReason": filter_reason,
        "TradesToday":  daily_trades_taken,
        "Rec1": f"{recs[0].strike}{recs[0].opt_type}@{recs[0].premium}",
        "Rec2": f"{recs[1].strike}{recs[1].opt_type}@{recs[1].premium}",
        "Rec3": f"{recs[2].strike}{recs[2].opt_type}@{recs[2].premium}",
        "PCR_Bands":    f"{PCR_BULLISH}/{PCR_BEARISH}",
    }])
    log_row.to_csv(LOG_FILE, mode="a",
                   header=not os.path.exists(LOG_FILE), index=False)

    render(df, symbol, spot, vix, expiry, pcr_for_bias, bias, max_pain,
           resistance, support, weighted_net_score, roc_alerts, recs, demo, cycle,
           score=score, score_breakdown=breakdown, taken=take,
           skip_reason=filter_reason, unanimous=unanimous,
           rsi=rsi_val, vwap=vwap_val, tech_signal=tech_signal,
           pcr_signal=pcr_signal, pcr_signal_color=pcr_signal_color,
           local_pcr=local_pcr)

    return Signal(
        time=now_ist().strftime("%H:%M"),
        bias=bias, spot=spot, pcr=pcr_for_bias,
        rec1=recs[0], rec2=recs[1], rec3=recs[2],
        score=score, taken=take, skip_reason=filter_reason,
        votes_unanimous=unanimous,
        rsi=rsi_val, vwap=vwap_val, tech_signal=tech_signal,
    )


# ────────────────────────────────────────────────────────────────
#  SECTION 11 — Main Loop
# ────────────────────────────────────────────────────────────────
def main():
    global DISPLAY_MODE

    # ── CLI flag: python nse_oi_dashboard.py --gui ───────────────
    if "--gui" in sys.argv:
        DISPLAY_MODE = "tkinter"
    if "--terminal" in sys.argv:
        DISPLAY_MODE = "terminal"

    # ── Tkinter mode ─────────────────────────────────────────────
    if DISPLAY_MODE == "tkinter":
        if not _TK_AVAILABLE:
            print("ERROR: Tkinter not available in this environment.")
            print("  • Google Colab: use terminal mode (no --gui flag)")
            print("  • Local: ensure Python was installed with Tcl/Tk")
            print("Falling back to terminal mode.")
            DISPLAY_MODE = "terminal"
        else:
            root = tk.Tk()
            app  = OITkApp(root)
            try:
                root.mainloop()
            except KeyboardInterrupt:
                pass
            finally:
                if signal_log:
                    last = signal_log[-1].spot
                    for s in signal_log:
                        if s.spot_exit is None:
                            s.spot_exit = last
                    run_eod_backtest(last)
                print(f"Log saved -> {LOG_FILE}")
            return

    # ── Terminal / Colab mode (default) ──────────────────────────
    market_open = is_market_open()
    use_demo    = (True  if DEMO_MODE is True  else
                   False if DEMO_MODE is False else
                   not market_open)

    print("=" * 68)
    print("  NSE LIVE OI DASHBOARD  v5.1")
    print(f"  Env: {'Colab' if IN_COLAB else 'Local'}  |  "
          f"Symbol: {SYMBOL}  |  Lot: {LOT_SIZE}  |  Refresh: {REFRESH_RATE}s")
    print(f"  Mode: {'DEMO (markets closed)' if use_demo else 'LIVE'}")
    print(f"  RSI({RSI_PERIOD}) + VWAP({VWAP_WINDOW}): warms up after {RSI_PERIOD+1} cycles")
    print(f"  4-vote bias: PCR + OI Score + Max Pain + RSI/VWAP")
    print(f"  GUI available: run with --gui flag for Tkinter window")
    if use_demo:
        print(f"  Next market open: {next_open_str()}")
    print("=" * 68)

    session  = None if use_demo else create_session()
    cycle    = 0
    eod_done = False

    while True:
        try:
            cycle += 1

            if use_demo:
                vix  = str(round(
                    14.5 + math.sin(cycle * 0.5) * 2.5
                    + random.uniform(-0.3, 0.3), 2))
                data = demo_data(SYMBOL, cycle)
            else:
                vix  = fetch_vix()
                data = fetch_chain(session, SYMBOL)
                if data is None:
                    print(f"No data. Next open: {next_open_str()}. "
                          f"Retrying in {REFRESH_RATE}s...")
                    if not is_market_open():
                        print("Switching to Demo Mode...")
                        use_demo = True
                    time.sleep(REFRESH_RATE)
                    continue
                if cycle % 10 == 0:
                    session = create_session()

            sig = process_cycle(data, SYMBOL, vix, use_demo, cycle)

            if sig:
                signal_log.append(sig)
                # Feed previous signal's outcome for auto-tuner
                if len(signal_log) >= 2:
                    prev = signal_log[-2]
                    prev.spot_exit = sig.spot
                    prev.outcome   = (
                        "WIN" if
                        (prev.bias == "BULLISH" and sig.spot > prev.spot)
                        or (prev.bias == "BEARISH" and sig.spot < prev.spot)
                        else "LOSS"
                    )
                    auto_tune(prev)

            # EOD backtest
            if is_eod() and not eod_done and not use_demo:
                eod_done = True
                final_spot = data["records"]["underlyingValue"]
                for s in signal_log:
                    if s.spot_exit is None:
                        s.spot_exit = final_spot
                run_eod_backtest(final_spot)

            time.sleep(5 if use_demo else REFRESH_RATE)

        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            if signal_log:
                last = signal_log[-1].spot
                for s in signal_log:
                    if s.spot_exit is None:
                        s.spot_exit = last
                run_eod_backtest(last)
            print(f"Log saved -> {LOG_FILE}")
            break
        except Exception as e:
            print(f"Error: {e}  retrying in 15s...")
            time.sleep(15)


if __name__ == "__main__":
    main()
