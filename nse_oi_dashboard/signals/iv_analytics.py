# ════════════════════════════════════════════════════════════════
#  signals/iv_analytics.py  — v5.3 NEW MODULE
#
#  Implied Volatility analytics inspired by Sensibull / Opstra UI:
#
#    ┌──────────────────────────────────────────────────────────┐
#    │  Metric       Description                                │
#    ├──────────────────────────────────────────────────────────┤
#    │  ATM IV       Implied vol at the ATM strike (CE+PE avg)  │
#    │  IVR          IV Rank — position in 52-week range (%)    │
#    │  IVP          IV Percentile — % of days IV < today       │
#    │  Daily IV     Intraday IV trend (high/low/avg/current)   │
#    │  IV Skew      OTM Put IV − OTM Call IV                   │
#    │  Skew side    PUT HEAVY / CALL HEAVY / BALANCED          │
#    └──────────────────────────────────────────────────────────┘
#
#  Data flow:
#    build_df() (nse_fetcher) → DataFrame with CE_IV / PE_IV columns
#    → calc_atm_iv()          → ATM IV float
#    → IVTracker.record()     → daily IV trend
#    → calc_iv_skew()         → skew % + direction label
#    → IVHistory.update()     → persistent 252-day store → IVR / IVP
#
#  Historical IV storage: SYMBOL_iv_history.csv  (auto-created)
#    Columns: Date, ATM_IV, VIX
#    Bootstrapped from yfinance ^INDIAVIX on first run.
# ════════════════════════════════════════════════════════════════

import os
import math
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config import (
    SYMBOL, IV_HISTORY_DAYS, IV_HIST_FILE,
    IV_SKEW_OTM_STRIKES,
    IV_SKEW_PUT_HEAVY_THRESHOLD,
    IV_SKEW_CALL_HEAVY_THRESHOLD,
    IV_DAILY_ALERT_SPIKE,
)
from core.nse_fetcher import nearest_strike, strike_step
from core.market_hours import now_ist


# ════════════════════════════════════════════════════════════════
#  1.  ATM IV
# ════════════════════════════════════════════════════════════════
def calc_atm_iv(df: pd.DataFrame, spot: float) -> Optional[float]:
    """
    Return the average of CE_IV and PE_IV at the ATM strike.
    Uses impliedVolatility field from NSE option chain data.

    Returns None if the data is zero (warmup / missing).
    """
    if "CE_IV" not in df.columns or "PE_IV" not in df.columns:
        return None
    atm = nearest_strike(spot, SYMBOL)
    row = df[df["Strike"] == float(atm)]
    if row.empty:
        # fallback to closest available strike
        row = df.iloc[(df["Strike"] - atm).abs().argsort()[:1]]
    ce_iv = float(row["CE_IV"].iloc[0])
    pe_iv = float(row["PE_IV"].iloc[0])
    if ce_iv == 0 and pe_iv == 0:
        return None
    if ce_iv == 0:
        return round(pe_iv, 2)
    if pe_iv == 0:
        return round(ce_iv, 2)
    return round((ce_iv + pe_iv) / 2, 2)


# ════════════════════════════════════════════════════════════════
#  2.  IV Skew  (which side is heavy)
# ════════════════════════════════════════════════════════════════
def calc_iv_skew(df: pd.DataFrame, spot: float,
                 n_otm: int = IV_SKEW_OTM_STRIKES) -> dict:
    """
    Calculate IV skew using the n_otm strikes on each side of ATM.

    Logic:
      OTM_PUT_IV  = avg PE_IV of (ATM-1), (ATM-2), ... (ATM-n_otm) strikes
      OTM_CALL_IV = avg CE_IV of (ATM+1), (ATM+2), ... (ATM+n_otm) strikes
      skew        = OTM_PUT_IV − OTM_CALL_IV

    Interpretation (same as Sensibull IV Skew %):
      PUT HEAVY  (skew >  +2%): OTM puts more expensive than OTM calls
                                 → market fears downside; put writers demand premium
      CALL HEAVY (skew < -2%): OTM calls more expensive than OTM puts
                                 → market fears upside breakout / short squeeze
      BALANCED   (|skew| ≤ 2%): symmetric demand on both sides

    Returns dict:
      atm_iv, otm_put_iv, otm_call_iv, skew_pct, direction, color
    """
    if "CE_IV" not in df.columns or "PE_IV" not in df.columns:
        return _empty_skew()

    step = strike_step(SYMBOL)
    atm  = nearest_strike(spot, SYMBOL)
    strikes = sorted(df["Strike"].unique())

    # Gather OTM put IV (strikes below ATM)
    put_ivs = []
    for k in range(1, n_otm + 1):
        tgt = float(atm - k * step)
        row = df[df["Strike"] == tgt]
        if row.empty:
            # fallback: closest below
            below = df[df["Strike"] < float(atm)]
            if below.empty:
                continue
            row = below.iloc[-k:].head(1)
        iv = float(row["PE_IV"].iloc[0]) if not row.empty else 0
        if iv > 0:
            put_ivs.append(iv)

    # Gather OTM call IV (strikes above ATM)
    call_ivs = []
    for k in range(1, n_otm + 1):
        tgt = float(atm + k * step)
        row = df[df["Strike"] == tgt]
        if row.empty:
            above = df[df["Strike"] > float(atm)]
            if above.empty:
                continue
            row = above.head(k).tail(1)
        iv = float(row["CE_IV"].iloc[0]) if not row.empty else 0
        if iv > 0:
            call_ivs.append(iv)

    if not put_ivs or not call_ivs:
        return _empty_skew()

    otm_put_iv  = round(sum(put_ivs)  / len(put_ivs),  2)
    otm_call_iv = round(sum(call_ivs) / len(call_ivs), 2)
    skew_pct    = round(otm_put_iv - otm_call_iv, 2)

    if skew_pct > IV_SKEW_PUT_HEAVY_THRESHOLD:
        direction = "PUT HEAVY"
        color     = "#e74c3c"    # red — downside fear premium
    elif skew_pct < IV_SKEW_CALL_HEAVY_THRESHOLD:
        direction = "CALL HEAVY"
        color     = "#2ecc71"    # green — upside fear / squeeze premium
    else:
        direction = "BALANCED"
        color     = "#f39c12"    # orange — neutral skew

    atm_iv = calc_atm_iv(df, spot)

    return {
        "atm_iv":      atm_iv,
        "otm_put_iv":  otm_put_iv,
        "otm_call_iv": otm_call_iv,
        "skew_pct":    skew_pct,
        "direction":   direction,
        "color":       color,
    }


def _empty_skew() -> dict:
    return {
        "atm_iv": None, "otm_put_iv": None, "otm_call_iv": None,
        "skew_pct": 0.0, "direction": "N/A", "color": "#8b949e",
    }


# ════════════════════════════════════════════════════════════════
#  3.  Daily IV Tracker  (intraday session tracking)
# ════════════════════════════════════════════════════════════════
class IVTracker:
    """
    Tracks ATM IV readings through the trading session.
    Provides intraday high / low / average / current IV
    and fires a spike alert when IV rises sharply in one cycle.

    Usage:
        tracker = IVTracker()
        result  = tracker.record(atm_iv)
        # result.get('spike_alert') is True when IV jumped > threshold
    """
    def __init__(self):
        self.readings: list = []    # list of (time_str, atm_iv)
        self.prev_iv: Optional[float] = None

    def record(self, atm_iv: Optional[float]) -> dict:
        """Record a new ATM IV observation; return daily summary dict."""
        if atm_iv is None:
            return self._summary()
        now = now_ist().strftime("%H:%M")
        self.readings.append((now, atm_iv))

        spike_alert = False
        if (self.prev_iv is not None and self.prev_iv > 0):
            pct_change = (atm_iv - self.prev_iv) / self.prev_iv * 100
            if pct_change >= IV_DAILY_ALERT_SPIKE:
                spike_alert = True
        self.prev_iv = atm_iv
        return {**self._summary(), "spike_alert": spike_alert}

    def _summary(self) -> dict:
        if not self.readings:
            return {"current": None, "high": None, "low": None,
                    "avg": None, "open": None, "spike_alert": False,
                    "readings": []}
        ivs = [iv for _, iv in self.readings]
        return {
            "current":  ivs[-1],
            "open":     ivs[0],
            "high":     max(ivs),
            "low":      min(ivs),
            "avg":      round(sum(ivs) / len(ivs), 2),
            "spike_alert": False,
            "readings": self.readings,    # for charting
        }

    def reset(self):
        self.readings = []
        self.prev_iv  = None


# ════════════════════════════════════════════════════════════════
#  4.  Historical IV  →  IVR + IVP
# ════════════════════════════════════════════════════════════════
class IVHistory:
    """
    Manages a rolling 252-trading-day IV history for computing
    IV Rank (IVR) and IV Percentile (IVP).

    Storage: SYMBOL_iv_history.csv  (one row per trading day)
    On first run, bootstraps from yfinance India VIX data.

    IVR = (current_IV - 52w_low)  / (52w_high - 52w_low) × 100
    IVP = (days where IV < current_IV) / total_days × 100

    These match the Sensibull / Opstra platform values visible in screenshots.
    """

    def __init__(self, filepath: str = IV_HIST_FILE):
        self.filepath = filepath
        self.history: pd.DataFrame = self._load_or_bootstrap()

    # ── Load / bootstrap ─────────────────────────────────────────
    def _load_or_bootstrap(self) -> pd.DataFrame:
        if os.path.exists(self.filepath):
            try:
                df = pd.read_csv(self.filepath, parse_dates=["Date"])
                if len(df) >= 5:
                    return df.tail(IV_HISTORY_DAYS).reset_index(drop=True)
            except Exception:
                pass
        return self._bootstrap_from_vix()

    def _bootstrap_from_vix(self) -> pd.DataFrame:
        """
        Use India VIX (^INDIAVIX) from yfinance as IV proxy.
        For NIFTY/BANKNIFTY, VIX is a reliable IV proxy for the index.
        Saves to CSV so subsequent runs are instant.
        """
        print(f"[IVHistory] Bootstrapping {IV_HISTORY_DAYS}-day IV history from VIX...")
        try:
            import yfinance as yf
            vix = yf.Ticker("^INDIAVIX").history(period="2y")
            if vix.empty:
                raise ValueError("Empty VIX data")
            df = pd.DataFrame({
                "Date":   vix.index.tz_localize(None),
                "ATM_IV": vix["Close"].round(2).values,
                "VIX":    vix["Close"].round(2).values,
            }).tail(IV_HISTORY_DAYS).reset_index(drop=True)
            df.to_csv(self.filepath, index=False)
            print(f"[IVHistory] Saved {len(df)} days to {self.filepath}")
            return df
        except Exception as e:
            print(f"[IVHistory] Bootstrap failed ({e}) — IVR/IVP unavailable until data builds up.")
            return pd.DataFrame(columns=["Date", "ATM_IV", "VIX"])

    # ── Update with today's closing IV ──────────────────────────
    def update(self, atm_iv: float, date: datetime = None):
        """
        Append today's ATM IV to the history (call once at session end).
        Automatically trims to IV_HISTORY_DAYS.
        """
        if atm_iv is None or atm_iv <= 0:
            return
        dt = (date or now_ist()).date()
        # Avoid duplicate for same date
        if not self.history.empty and len(self.history) > 0:
            last_date = pd.to_datetime(self.history["Date"].iloc[-1]).date()
            if last_date == dt:
                self.history.iloc[-1, self.history.columns.get_loc("ATM_IV")] = atm_iv
            else:
                new_row = pd.DataFrame([{"Date": dt, "ATM_IV": atm_iv, "VIX": atm_iv}])
                self.history = pd.concat([self.history, new_row], ignore_index=True)
        else:
            new_row = pd.DataFrame([{"Date": dt, "ATM_IV": atm_iv, "VIX": atm_iv}])
            self.history = pd.concat([self.history, new_row], ignore_index=True)

        self.history = self.history.tail(IV_HISTORY_DAYS).reset_index(drop=True)
        try:
            self.history.to_csv(self.filepath, index=False)
        except Exception:
            pass

    # ── IVR + IVP ────────────────────────────────────────────────
    def calc_ivr(self, current_iv: float) -> Optional[float]:
        """
        IV Rank = (current - 52w_low) / (52w_high - 52w_low) × 100
        High IVR (>50) = IV is elevated; selling premium is historically expensive.
        Low  IVR (<25) = IV is cheap; buying premium has better expected value.
        """
        if self.history.empty or current_iv is None:
            return None
        ivs = self.history["ATM_IV"].dropna().values
        if len(ivs) < 5:
            return None
        iv_high = float(ivs.max())
        iv_low  = float(ivs.min())
        if iv_high == iv_low:
            return 50.0
        return round((current_iv - iv_low) / (iv_high - iv_low) * 100, 1)

    def calc_ivp(self, current_iv: float) -> Optional[float]:
        """
        IV Percentile = % of days in history where IV was below current IV.
        IVP 70 means IV was below today's level 70% of the time last year.
        """
        if self.history.empty or current_iv is None:
            return None
        ivs = self.history["ATM_IV"].dropna().values
        if len(ivs) < 5:
            return None
        days_below = sum(1 for iv in ivs if iv < current_iv)
        return round(days_below / len(ivs) * 100, 1)

    def summary(self, current_iv: float) -> dict:
        """Full IV analytics summary dict for display."""
        ivr = self.calc_ivr(current_iv)
        ivp = self.calc_ivp(current_iv)
        return {
            "current_iv": current_iv,
            "ivr":        ivr,
            "ivp":        ivp,
            "hist_high":  round(float(self.history["ATM_IV"].max()), 2) if not self.history.empty else None,
            "hist_low":   round(float(self.history["ATM_IV"].min()), 2) if not self.history.empty else None,
            "hist_avg":   round(float(self.history["ATM_IV"].mean()), 2) if not self.history.empty else None,
            "data_days":  len(self.history),
        }


# ════════════════════════════════════════════════════════════════
#  5.  IV signal interpretation  (for display + scoring)
# ════════════════════════════════════════════════════════════════
def interpret_iv(ivr: Optional[float], ivp: Optional[float],
                 atm_iv: Optional[float]) -> dict:
    """
    Convert raw IV metrics into actionable interpretation labels.

    Returns:
      regime: "HIGH IV" | "LOW IV" | "NORMAL IV"
      strategy_hint: plain-language suggestion based on IV regime
      color: display color for the regime badge
    """
    if ivr is None:
        return {"regime": "N/A", "strategy_hint": "Building history...", "color": "#8b949e"}

    if ivr >= 50:
        regime = "HIGH IV"
        color  = "#e74c3c"
        hint   = "IV elevated → selling premium (short straddle/strangle) has edge"
    elif ivr <= 25:
        regime = "LOW IV"
        color  = "#2ecc71"
        hint   = "IV cheap → buying premium (directional long options) favourable"
    else:
        regime = "NORMAL IV"
        color  = "#f39c12"
        hint   = "IV in normal range → no strong edge for sellers or buyers"

    return {"regime": regime, "strategy_hint": hint, "color": color}


def format_iv_panel(skew: dict, daily: dict, hist_summary: dict) -> list:
    """
    Returns a list of formatted strings for terminal display.
    Used by display/terminal.py to add an IV section to the dashboard.
    """
    atm_iv  = hist_summary.get("current_iv")
    ivr     = hist_summary.get("ivr")
    ivp     = hist_summary.get("ivp")
    interp  = interpret_iv(ivr, ivp, atm_iv)

    lines = []
    a = lines.append
    W = 68
    a("─" * W)
    a("  IMPLIED VOLATILITY  (v5.3)")
    a("─" * W)

    # ATM IV line
    iv_str  = f"{atm_iv:.2f}%" if atm_iv else "warming..."
    ivr_str = f"{ivr:.1f}" if ivr is not None else "N/A"
    ivp_str = f"{ivp:.1f}" if ivp is not None else "N/A"
    a(f"  ATM IV: {iv_str:>8}  |  IVR: {ivr_str:>6}  |  IVP: {ivp_str:>6}  "
      f"|  Regime: [{interp['regime']}]")

    # Historical range
    h = hist_summary.get("hist_high"); l = hist_summary.get("hist_low")
    if h and l:
        a(f"  52w High: {h:.2f}%  |  52w Low: {l:.2f}%  "
          f"|  52w Avg: {hist_summary.get('hist_avg', 0):.2f}%  "
          f"|  [{hist_summary.get('data_days', 0)} days data]")

    # Strategy hint
    a(f"  Hint: {interp['strategy_hint']}")

    # IV Skew
    skew_str = (f"{skew['skew_pct']:+.2f}%" if skew.get("skew_pct") is not None else "N/A")
    dir_str  = skew.get("direction", "N/A")
    put_iv   = f"{skew['otm_put_iv']:.2f}%" if skew.get("otm_put_iv") else "N/A"
    call_iv  = f"{skew['otm_call_iv']:.2f}%" if skew.get("otm_call_iv") else "N/A"
    a(f"  IV Skew: {skew_str:>8}  →  [{dir_str}]  "
      f"(OTM Put: {put_iv}  |  OTM Call: {call_iv})")

    # Daily IV tracker
    if daily.get("current") is not None:
        a(f"  Daily IV — Open: {daily['open']:.2f}%  "
          f"High: {daily['high']:.2f}%  Low: {daily['low']:.2f}%  "
          f"Avg: {daily['avg']:.2f}%  Current: {daily['current']:.2f}%")
        if daily.get("spike_alert"):
            a(f"  ⚠  IV SPIKE ALERT: sharp IV rise detected this cycle!")
    else:
        a("  Daily IV: warming up...")

    a("─" * W)
    return lines
