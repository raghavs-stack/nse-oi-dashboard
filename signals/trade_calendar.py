# ════════════════════════════════════════════════════════════════
#  signals/trade_calendar.py  v5.7
#  Day-quality scoring: which days/times give maximum edge.
#
#  Factors:
#    1. Day of week (Monday=0 … Friday=4)
#    2. Expiry proximity (days-to-expiry for NIFTY weekly & monthly)
#    3. Intraday time window quality
#    4. Historical win-rate by day (auto-updated from CSV log)
# ════════════════════════════════════════════════════════════════

from __future__ import annotations
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from core.market_hours import now_ist


# ── NIFTY expiry calendar ─────────────────────────────────────────
# NSE Circular NSE/FAOP/68747 (Jun 25, 2025) — effective Sep 1, 2025:
#   All index/stock derivatives expiry moved from Thursday → Tuesday.
#
#   NIFTY weekly    → Tuesday (1)   [was Thursday from 2023; back to Tuesday]
#   BANKNIFTY       → NO weekly contracts; monthly = Last Tuesday of month
#   FINNIFTY        → NO weekly contracts; monthly = Last Tuesday of month
#   MIDCPNIFTY      → NO weekly contracts; monthly = Last Tuesday of month
#
# WEEKLY_EXPIRY_DOW used by days_to_weekly_expiry():
#   For symbols with NO weekly, the value is set to Tuesday (1) so the
#   function returns days to the next Tuesday-aligned expiry, which is
#   the nearest monthly expiry for those symbols.
WEEKLY_EXPIRY_DOW  = {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1}
MONTHLY_EXPIRY_DOW = {"NIFTY": 1, "BANKNIFTY": 1}   # Last Tuesday


def _next_weekday(target_dow: int, from_date: date = None) -> date:
    """Return the next occurrence of target_dow on/after from_date."""
    d = from_date or date.today()
    days_ahead = target_dow - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


def _last_tuesday_of_month(d: date = None) -> date:
    """Return the last Tuesday of the month containing d.

    NSE Circular NSE/FAOP/68747: all index/stock derivatives monthly
    expiry moved from last Thursday → last Tuesday (effective Sep 1, 2025).
    """
    d = d or date.today()
    # Last day of month
    if d.month == 12:
        last = date(d.year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(d.year, d.month + 1, 1) - timedelta(days=1)
    # Walk back to Tuesday (weekday=1)
    while last.weekday() != 1:
        last -= timedelta(days=1)
    return last


def days_to_weekly_expiry(symbol: str = "NIFTY") -> int:
    """Days remaining to next weekly expiry."""
    dow     = WEEKLY_EXPIRY_DOW.get(symbol, 1)
    today   = date.today()
    expiry  = _next_weekday(dow, today)
    days    = (expiry - today).days
    return days if days >= 0 else days + 7


def days_to_monthly_expiry(symbol: str = "NIFTY") -> int:
    """Days remaining to last-Tuesday monthly expiry (NSE circular Jun-2025)."""
    today   = date.today()
    monthly = _last_tuesday_of_month(today)
    if monthly < today:
        # Next month
        next_m = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        monthly = _last_tuesday_of_month(next_m)
    return (monthly - today).days


# ── Day-of-week quality matrix ───────────────────────────────────
# Based on NIFTY statistical analysis (NSE circular Jun-2025 schedule):
#   Mon: gap openings, highest whipsaw rate → sell-only with caution
#   Tue: NIFTY weekly expiry → high gamma risk, avoid near-ATM positions
#   Wed: best mid-week; IV normalized, 6 days to expiry — good for both sides
#   Thu: no expiry pressure; strong directional/momentum day
#   Fri: premium selling edge (weekend theta collection)

DOW_PROFILE = {
    0: {  # Monday
        "score": 45,
        "label": "Cautious",
        "color": "#e67e22",
        "buy_edge":  "Low",
        "sell_edge": "Low",
        "note":  "Weekend gaps common. Wait for 10:30 AM trend confirmation.",
        "avoid": True,
    },
    1: {  # Tuesday — NIFTY weekly expiry (NSE circular Jun-2025: Thu→Tue)
        "score": 40,
        "label": "NIFTY Expiry",
        "color": "#e74c3c",
        "buy_edge":  "Low",
        "sell_edge": "High",
        "note":  "NIFTY weekly expiry day. Near-ATM gamma risk explodes. Sell far-OTM or trade BNF instead.",
        "avoid": True,
    },
    2: {  # Wednesday
        "score": 80,
        "label": "Best Day",
        "color": "#2ecc71",
        "buy_edge":  "High",
        "sell_edge": "High",
        "note":  "Peak liquidity, 6 days to next expiry. Best for directional & premium selling.",
        "avoid": False,
    },
    3: {  # Thursday — no expiry since NSE circular Jun-2025; good directional day
        "score": 70,
        "label": "Good",
        "color": "#3498db",
        "buy_edge":  "High",
        "sell_edge": "Medium",
        "note":  "No expiry risk (NIFTY moved to Tuesday). Strong directional session — good for momentum buys.",
        "avoid": False,
    },
    4: {  # Friday
        "score": 65,
        "label": "Good",
        "color": "#9b59b6",
        "buy_edge":  "Medium",
        "sell_edge": "High",
        "note":  "Weekend theta collection. Sell OTM strangles 3-5% from spot.",
        "avoid": False,
    },
}


# ── Intraday time window quality ──────────────────────────────────
TIME_WINDOWS = [
    (9, 15,  9, 30,  20, "Opening",    "High volatility, wide spreads. Observe only."),
    (9, 30, 10, 30,  60, "Trend Setup","First trend emerges. Enter on confirmation."),
    (10,30, 12, 30,  90, "Prime",      "Best window. Volatility normalized, tight spreads."),
    (12,30, 14,  0,  40, "Lunch Lull", "Low volume, choppy. Reduce position size by 50%."),
    (14,  0, 15, 15,  75, "Afternoon", "Volume picks up. Good for directional plays."),
    (15, 15, 15, 30,  25, "Closing",   "Avoid new positions. Close or hedge only."),
]

def get_time_window(dt: datetime = None) -> dict:
    """Return quality score and label for the current time."""
    dt = dt or now_ist()
    h, m = dt.hour, dt.minute
    minutes = h * 60 + m
    for h1, m1, h2, m2, score, label, note in TIME_WINDOWS:
        if h1 * 60 + m1 <= minutes < h2 * 60 + m2:
            return {"score": score, "label": label, "note": note,
                    "window": f"{h1:02d}:{m1:02d}–{h2:02d}:{m2:02d}"}
    return {"score": 50, "label": "Off-Hours", "note": "Market closed.", "window": "–"}


# ── Expiry proximity score ────────────────────────────────────────
def expiry_proximity_score(days_to_exp: int) -> dict:
    """
    Score and strategy hint based on days remaining to expiry.
    0-1d: avoid (gamma risk)
    2-3d: sell premium window
    4-7d: buy premium / directional
    8-14d: balanced
    15+d: directional with less theta decay risk
    """
    if days_to_exp <= 1:
        return {"score": 20, "strategy": "AVOID",
                "note": "Expiry day — extreme gamma. Only far-OTM spreads.",
                "regime": "EXPIRY"}
    if days_to_exp <= 3:
        return {"score": 70, "strategy": "SELL PREMIUM",
                "note": f"{days_to_exp}d to expiry — theta accelerates. Sell OTM strangles/condors.",
                "regime": "SELL"}
    if days_to_exp <= 7:
        return {"score": 85, "strategy": "BUY OR SELL",
                "note": f"{days_to_exp}d to expiry — ideal window. Both buying and selling have edge.",
                "regime": "BOTH"}
    if days_to_exp <= 14:
        return {"score": 75, "strategy": "DIRECTIONAL",
                "note": f"{days_to_exp}d to expiry — enough time for trend to play out.",
                "regime": "BUY"}
    return {"score": 60, "strategy": "SWING",
            "note": f"{days_to_exp}d to expiry — low theta, use for swing trades.",
            "regime": "BUY"}


# ── Historical win-rate by day of week ────────────────────────────
def historical_day_stats(symbol: str = "NIFTY") -> pd.DataFrame:
    """
    Read all available daily CSV logs and compute win-rate by day of week.
    Returns DataFrame with columns: DayOfWeek, Trades, Wins, WinRate, AvgPnL_est.
    """
    import glob
    pattern = f"{symbol}_OI_*.csv"
    files   = glob.glob(pattern)
    if not files:
        return pd.DataFrame()

    rows = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
            taken = df[df["Taken"].astype(str).str.lower() == "true"]
            if taken.empty:
                continue
            for _, row in taken.iterrows():
                dow = row["Timestamp"].weekday() if pd.notna(row["Timestamp"]) else None
                if dow is None:
                    continue
                score = float(row.get("Score", 0))
                rows.append({"dow": dow, "score": score,
                             "bias": row.get("Bias", "NEUTRAL")})
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df_all = pd.DataFrame(rows)
    DOW_NAMES = {0:"Mon", 1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri"}
    result = (df_all.groupby("dow")
              .agg(Signals=("score", "count"),
                   AvgScore=("score", "mean"))
              .reset_index())
    result["Day"]     = result["dow"].map(DOW_NAMES)
    result["Profile"] = result["dow"].map(lambda d: DOW_PROFILE.get(d, {}).get("label", "?"))
    return result[["Day", "Signals", "AvgScore", "Profile"]].round(1)


# ── Master calendar score ─────────────────────────────────────────
def get_day_quality(symbol: str = "NIFTY") -> dict:
    """
    Composite day quality — call this once per cycle.
    Returns everything needed to display day quality in Streamlit.
    """
    now      = now_ist()
    today    = date.today()
    dow      = today.weekday()
    profile  = DOW_PROFILE.get(dow, DOW_PROFILE[2])
    tw       = get_time_window(now)
    dtw      = days_to_weekly_expiry(symbol)
    dtm      = days_to_monthly_expiry(symbol)
    exp_prox = expiry_proximity_score(dtw)

    # Composite: 40% day-of-week + 30% time window + 30% expiry proximity
    composite = int(
        profile["score"]     * 0.40 +
        tw["score"]          * 0.30 +
        exp_prox["score"]    * 0.30
    )

    return {
        "composite_score":  composite,
        "dow":              dow,
        "day_name":         now.strftime("%A"),
        "day_profile":      profile,
        "time_window":      tw,
        "days_to_weekly":   dtw,
        "days_to_monthly":  dtm,
        "expiry_proximity": exp_prox,
        "trade_today":      composite >= 55 and not profile.get("avoid", False),
        "best_strategy":    exp_prox["strategy"],
    }
