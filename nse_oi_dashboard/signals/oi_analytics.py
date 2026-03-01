# ════════════════════════════════════════════════════════════════
#  signals/oi_analytics.py
#  OI-based analytics: max pain, localized PCR, 5-level signal,
#  RoC alerts, 8-factor signal scorer, trade filter, recommender,
#  and PCR auto-tuner.
# ════════════════════════════════════════════════════════════════

import pandas as pd
from typing import Optional

import state
from config import (
    PCR_THRESHOLDS, LOCALIZED_PCR_RANGE,
    ROC_THRESHOLD, REFRESH_RATE,
    MAX_TRADES_PER_DAY, MIN_SIGNAL_SCORE, MIN_TRADE_GAP_MINS,
    LOT_SIZE,
)
from core.market_hours import now_ist
from core.nse_fetcher import nearest_strike, strike_step


# ────────────────────────────────────────────────────────────────
#  Max Pain
# ────────────────────────────────────────────────────────────────
def calc_max_pain(df: pd.DataFrame) -> float:
    """
    Classic max-pain calculation: strike where total option writer loss is minimised.
    Returns the strike price (float).
    """
    best_strike, best_loss = None, float("inf")
    for exp in df["Strike"]:
        loss = (
            (df["CE_OI"] * (exp - df["Strike"]).clip(lower=0)).sum()
            + (df["PE_OI"] * (df["Strike"] - exp).clip(lower=0)).sum()
        )
        if loss < best_loss:
            best_loss, best_strike = loss, exp
    return float(best_strike)


# ────────────────────────────────────────────────────────────────
#  Localized PCR
# ────────────────────────────────────────────────────────────────
def calc_localized_pcr(df: pd.DataFrame, spot: float) -> Optional[float]:
    """
    PCR computed only on ATM ±LOCALIZED_PCR_RANGE strikes.
    Port of NsePcrSignal.calculate_localized_pcr() from nse.py.
    Filters out far-OTM noise that distorts full-chain PCR.
    """
    strikes = sorted(df["Strike"].tolist())
    if not strikes:
        return None
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    start = max(0, atm_idx - LOCALIZED_PCR_RANGE)
    end   = min(len(strikes), atm_idx + LOCALIZED_PCR_RANGE + 1)
    local = df[df["Strike"].isin(strikes[start:end])]
    total_ce = local["CE_OI"].sum()
    if total_ce == 0:
        return None
    return round(local["PE_OI"].sum() / total_ce, 3)


# ────────────────────────────────────────────────────────────────
#  5-Level PCR Signal  (NsePcrSignal.generate_signal port)
# ────────────────────────────────────────────────────────────────
def generate_pcr_signal(pcr: Optional[float]) -> tuple[str, str]:
    """
    Returns (signal_text, color_hex).
    Contrarian: low PCR (call writing) → market oversold → BUY
                high PCR (put writing) → market overbought → SELL
    """
    if pcr is None:
        return "NEUTRAL", "#f39c12"
    if pcr <= PCR_THRESHOLDS["STRONG_BUY"]:
        return "STRONG BUY",  "#006400"
    if pcr <= PCR_THRESHOLDS["BUY"]:
        return "BUY",         "#2ecc71"
    if pcr >= PCR_THRESHOLDS["STRONG_SELL"]:
        return "STRONG SELL", "#8b0000"
    if pcr >= PCR_THRESHOLDS["SELL"]:
        return "SELL",        "#e74c3c"
    return "NEUTRAL", "#f39c12"


# ────────────────────────────────────────────────────────────────
#  RoC Alerts
# ────────────────────────────────────────────────────────────────
def compute_roc_alerts(data_items: list, expiry: str) -> list[str]:
    """Detect strikes with sudden OI buildup (> ROC_THRESHOLD per cycle)."""
    alerts, new_state = [], {}
    for item in data_items:
        if item.get("expiryDate") != expiry:
            continue
        s  = item["strikePrice"]
        co = item.get("CE", {}).get("openInterest", 0)
        po = item.get("PE", {}).get("openInterest", 0)
        new_state[s] = {"CE": co, "PE": po}
        if s in state.prev_oi:
            ce_d = co - state.prev_oi[s]["CE"]
            pe_d = po - state.prev_oi[s]["PE"]
            if ce_d > ROC_THRESHOLD:
                alerts.append(f"CALL BUILDUP  Strike {int(s):,}  +{ce_d:,} OI/{REFRESH_RATE}s")
            if pe_d > ROC_THRESHOLD:
                alerts.append(f"PUT  BUILDUP  Strike {int(s):,}  +{pe_d:,} OI/{REFRESH_RATE}s")
    state.prev_oi = new_state
    return alerts


# ────────────────────────────────────────────────────────────────
#  Signal Scorer  (8 factors, 0–100 pts)
# ────────────────────────────────────────────────────────────────
#  Factor                              Max pts
#  1  Bias unanimity (all 4 agree)       20
#  2  PCR extremity (dist from 1.0)      15
#  3  Net OI score magnitude             15
#  4  Max pain alignment                 10
#  5  VIX zone (12-18 ideal)             10
#  6  Time of day                        10
#  7  RoC confirmation                    5
#  8  RSI+VWAP confirmation              15
#  ─────────────────────────────────────────
#  Total                                100
def score_signal(bias, votes, pcr, net_score, spot, max_pain,
                 vix_str, roc_alerts, tech_signal="NEUTRAL") -> tuple:
    """Returns (score: int, breakdown: dict, unanimous: bool)."""
    pts = {}

    unanimous          = len(set(votes)) == 1
    pts["unanimity"]   = 20 if unanimous else (10 if votes.count(bias) >= 3 else 0)
    pts["pcr"]         = min(15, int(abs(pcr - 1.0) * 38))
    pts["oi_score"]    = min(15, int(abs(net_score) / 300_000 * 15))

    pain_dist          = abs(spot - max_pain)
    moving_toward      = ((bias == "BULLISH" and spot < max_pain)
                          or (bias == "BEARISH" and spot > max_pain))
    pts["max_pain"]    = min(10, int(pain_dist / 50) * 2) if moving_toward else 0

    try:
        vix = float(vix_str)
        pts["vix"] = (10 if 12 <= vix <= 18 else
                      6  if 18 < vix <= 22  else
                      2  if vix > 22         else 4)
    except (ValueError, TypeError):
        pts["vix"] = 5

    n   = now_ist(); t = n.hour * 60 + n.minute
    pts["time"] = (0  if t < 9*60+30 or t > 14*60+45 else
                   10 if t <= 10*60+30                 else
                   8  if t <= 13*60                    else 5)

    roc_bonus = 0
    for alert in roc_alerts:
        if bias == "BULLISH" and "CALL" in alert: roc_bonus = 5; break
        if bias == "BEARISH" and "PUT"  in alert: roc_bonus = 5; break
    pts["roc"]       = roc_bonus
    pts["rsi_vwap"]  = (15 if tech_signal == bias else
                        5  if tech_signal == "NEUTRAL" else 0)

    return min(100, sum(pts.values())), pts, unanimous


# ────────────────────────────────────────────────────────────────
#  Trade Filter
# ────────────────────────────────────────────────────────────────
def should_take_trade(score: int, bias: str) -> tuple[bool, str]:
    """4-gate filter. Returns (take: bool, reason: str)."""
    if state.daily_trades_taken >= MAX_TRADES_PER_DAY:
        return False, f"Daily cap reached ({MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY})"
    if score < MIN_SIGNAL_SCORE:
        return False, f"Score {score}/100 < min {MIN_SIGNAL_SCORE}"
    if state.last_trade_time is not None:
        gap = (now_ist() - state.last_trade_time).seconds // 60
        if gap < MIN_TRADE_GAP_MINS:
            return False, f"Too soon after last trade ({gap}min < {MIN_TRADE_GAP_MINS}min gap)"
    taken = [s for s in state.signal_log if s.taken]
    if taken and taken[-1].bias == bias:
        return False, f"Same bias ({bias}) as last taken trade — wait for reversal"
    return True, "PASS"


def register_trade_taken():
    state.daily_trades_taken += 1
    state.last_trade_time = now_ist()


# ────────────────────────────────────────────────────────────────
#  Strike Recommender
# ────────────────────────────────────────────────────────────────
def recommend_strikes(df, spot, bias, max_pain, resistance, support,
                      symbol="NIFTY") -> list:
    """Return exactly 3 TradeRec recommendations."""
    from backtest.eod_backtest import TradeRec
    atm  = nearest_strike(spot, symbol)
    step = strike_step(symbol)

    def make_rec(label, strike, opt, reason):
        col  = "CE_LTP" if opt == "CE" else "PE_LTP"
        row  = df[df["Strike"] == float(strike)]
        prem = float(row[col].iloc[0]) if not row.empty else 0
        if prem < 0.5:
            prem = max(2.0, abs(strike - spot) * 0.003 + 8)
        return TradeRec(label, int(strike), opt, round(prem, 1),
                        round(prem * 0.50, 1), round(prem * 2.00, 1),
                        round(prem * LOT_SIZE, 0), "1:2", reason)

    if bias == "BULLISH":
        configs = [
            ("Conservative (ATM)", atm,          "CE", f"ATM CE | support @ {support:,} | pain {int(max_pain):,}"),
            ("Moderate (1-OTM)",   atm + step,   "CE", f"1 OTM CE | SL if < {support:,} | good delta"),
            ("Aggressive (2-OTM)", atm + step*2, "CE", f"2 OTM CE | breakout > {resistance:,}"),
        ]
    elif bias == "BEARISH":
        configs = [
            ("Conservative (ATM)", atm,          "PE", f"ATM PE | resistance @ {resistance:,} | pain {int(max_pain):,}"),
            ("Moderate (1-OTM)",   atm - step,   "PE", f"1 OTM PE | SL if > {resistance:,} | good delta"),
            ("Aggressive (2-OTM)", atm - step*2, "PE", f"2 OTM PE | breakdown < {support:,}"),
        ]
    else:
        configs = [
            ("Neutral-ATM CE", atm,        "CE", f"Neutral near {atm:,}; watch for breakout"),
            ("Neutral-ATM PE", atm,        "PE", f"Pair with CE for straddle"),
            ("Hedge OTM CE",   atm + step, "CE", f"Upside hedge > {atm+step:,}"),
        ]
    return [make_rec(*c) for c in configs]


# ────────────────────────────────────────────────────────────────
#  Auto-Tuner
# ────────────────────────────────────────────────────────────────
def auto_tune(sig):
    correct = (
        (sig.bias == "BULLISH" and sig.spot_exit > sig.spot)
        or (sig.bias == "BEARISH" and sig.spot_exit < sig.spot)
        or sig.bias == "NEUTRAL"
    )
    state.accuracy_window.append(correct)
    WINDOW = 20
    if len(state.accuracy_window) < WINDOW:
        return
    state.accuracy_window = state.accuracy_window[-WINDOW:]
    acc = sum(state.accuracy_window) / WINDOW
    if acc < 0.45:
        state.pcr_bearish = round(min(state.pcr_bearish + 0.05, 1.50), 2)
        state.pcr_bullish = round(max(state.pcr_bullish - 0.05, 0.60), 2)
        print(f"  AutoTune: acc={acc:.0%} → tightened [{state.pcr_bullish}–{state.pcr_bearish}]")
    elif acc > 0.65:
        state.pcr_bearish = round(max(state.pcr_bearish - 0.03, 1.10), 2)
        state.pcr_bullish = round(min(state.pcr_bullish + 0.03, 0.90), 2)
        print(f"  AutoTune: acc={acc:.0%} → loosened  [{state.pcr_bullish}–{state.pcr_bearish}]")
