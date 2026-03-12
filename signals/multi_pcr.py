# ════════════════════════════════════════════════════════════════
#  signals/multi_pcr.py  v5.7
#
#  Weekly PCR  — PCR of nearest weekly expiry contracts only
#  Monthly PCR — PCR of last-Thursday monthly expiry contracts
#  PCR Series  — Rolling time series with 20-EMA and VWAP
#
#  The Opstra-style insight:
#    Weekly PCR (near) shows INTRADAY hedging sentiment.
#    Monthly PCR (far) shows POSITIONAL / institutional view.
#    Divergence between the two is a key signal:
#      Weekly << Monthly → short-term sellers but long-term buyers → bounce likely
#      Weekly >> Monthly → short-term buyers but long-term sellers → fade rally
# ════════════════════════════════════════════════════════════════

from __future__ import annotations
from collections import deque
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd


# ── Expiry classification helpers ────────────────────────────────
def _parse_expiry_date(expiry_str: str) -> Optional[date]:
    """Parse Shoonya expiry string (e.g. '10-MAR-2026') to date."""
    try:
        return datetime.strptime(expiry_str.strip().title(), "%d-%b-%Y").date()
    except Exception:
        return None


def _is_monthly_expiry(d: date) -> bool:
    """True if d is the last Thursday of its month."""
    if d.weekday() != 3:  # must be Thursday
        return False
    # Is there another Thursday in this month?
    next_thu = d + timedelta(days=7)
    return next_thu.month != d.month


def classify_expiries(expiry_dates: list[str]) -> dict:
    """
    Given a list of expiry date strings from the option chain,
    classify them as weekly or monthly.
    Returns: {"weekly": str, "monthly": str, "all": [str, ...]}
    """
    parsed = []
    for s in expiry_dates:
        d = _parse_expiry_date(s)
        if d and d >= date.today():
            parsed.append((d, s))
    parsed.sort(key=lambda x: x[0])

    if not parsed:
        return {"weekly": None, "monthly": None, "all": []}

    weekly  = parsed[0][1]   # nearest = weekly
    monthly = None

    for d, s in parsed:
        if _is_monthly_expiry(d):
            monthly = s
            break

    # Fallback: if no classic monthly found, use second expiry
    if monthly is None and len(parsed) >= 2:
        monthly = parsed[1][1]

    return {
        "weekly":  weekly,
        "monthly": monthly,
        "all":     [s for _, s in parsed],
    }


# ── PCR computation per expiry ────────────────────────────────────
def calc_pcr_for_expiry(data_items: list, expiry: str) -> Optional[float]:
    """Compute PCR for a specific expiry from raw option chain data_items."""
    total_ce = total_pe = 0
    for item in data_items:
        if item.get("expiryDate") != expiry:
            continue
        total_ce += item.get("CE", {}).get("openInterest", 0)
        total_pe += item.get("PE", {}).get("openInterest", 0)
    if total_ce == 0:
        return None
    return round(total_pe / total_ce, 3)


# ── PCR Time Series with 20-EMA and VWAP ─────────────────────────
class PCRSeries:
    """
    Rolling time series of PCR values with indicators:
      - 20-period EMA
      - Intraday VWAP (PCR volume-weighted by total OI as proxy)
      - Divergence signal (weekly vs monthly PCR gap)

    Usage:
        series = PCRSeries()                    # singleton in main.py
        result = series.update(local_pcr, weekly_pcr, monthly_pcr, total_oi)
        # result keys: ema20, vwap, divergence, signal, history
    """

    EMA_PERIOD = 20

    def __init__(self, maxlen: int = 200):
        self.local   : deque[float] = deque(maxlen=maxlen)
        self.weekly  : deque[float] = deque(maxlen=maxlen)
        self.monthly : deque[float] = deque(maxlen=maxlen)
        self.oi_vol  : deque[float] = deque(maxlen=maxlen)   # OI as weight
        self.times   : deque[str]   = deque(maxlen=maxlen)
        self._ema20  : Optional[float] = None

    # ── EMA computation ───────────────────────────────────────────
    def _ema(self, new_val: float) -> float:
        k = 2 / (self.EMA_PERIOD + 1)
        if self._ema20 is None:
            if len(self.local) < self.EMA_PERIOD:
                # Not enough data yet — use SMA
                self._ema20 = sum(list(self.local)[-self.EMA_PERIOD:]) / len(self.local)
            else:
                self._ema20 = sum(list(self.local)[-self.EMA_PERIOD:]) / self.EMA_PERIOD
        self._ema20 = new_val * k + self._ema20 * (1 - k)
        return round(self._ema20, 4)

    # ── VWAP computation (intraday reset) ─────────────────────────
    def _vwap(self) -> float:
        """PCR VWAP = Σ(PCR × OI) / Σ(OI)"""
        vals = list(self.local)
        ois  = list(self.oi_vol)
        if not vals or not ois:
            return vals[-1] if vals else 0.0
        total_oi = sum(ois)
        if total_oi == 0:
            return vals[-1]
        return round(sum(p * o for p, o in zip(vals, ois)) / total_oi, 4)

    # ── Update ────────────────────────────────────────────────────
    def update(self, local_pcr: float, weekly_pcr: Optional[float],
               monthly_pcr: Optional[float], total_oi: float = 1e6,
               ts: str = None) -> dict:
        """
        Push a new cycle's PCR values and return computed indicators.
        """
        from core.market_hours import now_ist
        self.local.append(local_pcr)
        self.weekly.append(weekly_pcr  or local_pcr)
        self.monthly.append(monthly_pcr or local_pcr)
        self.oi_vol.append(max(total_oi, 1))
        self.times.append(ts or now_ist().strftime("%H:%M"))

        ema20 = self._ema(local_pcr)
        vwap  = self._vwap()

        # Divergence = weekly - monthly
        # Negative (weekly < monthly): near-term bearish, long-term bullish → fade
        # Positive (weekly > monthly): near-term bullish, long-term bearish → caution
        divergence = None
        div_signal = "NEUTRAL"
        if weekly_pcr and monthly_pcr:
            divergence = round(weekly_pcr - monthly_pcr, 3)
            if divergence < -0.15:
                div_signal = "BOUNCE LIKELY"   # short-term fear vs long-term calm
            elif divergence > 0.15:
                div_signal = "FADE RALLY"      # short-term euphoria vs long-term caution
            else:
                div_signal = "ALIGNED"

        # PCR trend: is local PCR rising or falling over last 5 cycles?
        trend = "FLAT"
        if len(self.local) >= 5:
            recent  = list(self.local)[-5:]
            if recent[-1] > recent[0] + 0.05:
                trend = "RISING ↑"    # more puts being written → bearish
            elif recent[-1] < recent[0] - 0.05:
                trend = "FALLING ↓"   # more calls being written → bullish

        # EMA cross signal
        cross_signal = "NEUTRAL"
        if len(self.local) >= 2:
            prev = list(self.local)[-2]
            if prev < ema20 and local_pcr >= ema20:
                cross_signal = "BEARISH CROSS"   # PCR crossed above EMA → bearish
            elif prev > ema20 and local_pcr <= ema20:
                cross_signal = "BULLISH CROSS"   # PCR crossed below EMA → bullish

        return {
            "local":       local_pcr,
            "weekly":      weekly_pcr,
            "monthly":     monthly_pcr,
            "ema20":       ema20,
            "vwap":        vwap,
            "divergence":  divergence,
            "div_signal":  div_signal,
            "trend":       trend,
            "cross_signal": cross_signal,
            "history": {
                "times":   list(self.times),
                "local":   list(self.local),
                "weekly":  list(self.weekly),
                "monthly": list(self.monthly),
                "ema20":   [self._compute_ema_series()[-1]] if self.local else [],
            }
        }

    def _compute_ema_series(self) -> list[float]:
        """Recompute full EMA series for charting."""
        vals = list(self.local)
        if not vals:
            return []
        k    = 2 / (self.EMA_PERIOD + 1)
        emas = [vals[0]]
        for v in vals[1:]:
            emas.append(v * k + emas[-1] * (1 - k))
        return [round(e, 4) for e in emas]

    def chart_data(self) -> dict:
        """Return full series data for Streamlit charting."""
        return {
            "times":   list(self.times),
            "local":   list(self.local),
            "weekly":  list(self.weekly),
            "monthly": list(self.monthly),
            "ema20":   self._compute_ema_series(),
            "vwap":    self._compute_vwap_series(),
        }

    def _compute_vwap_series(self) -> list[float]:
        """Running VWAP series for charting."""
        vals = list(self.local)
        ois  = list(self.oi_vol)
        if not vals:
            return []
        result, cum_pv, cum_v = [], 0.0, 0.0
        for p, v in zip(vals, ois):
            cum_pv += p * v
            cum_v  += v
            result.append(round(cum_pv / cum_v, 4) if cum_v else p)
        return result
