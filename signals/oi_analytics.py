# ════════════════════════════════════════════════════════════════
#  signals/oi_analytics.py
#  OI-based analytics: max pain, localized PCR, 5-level signal,
#  RoC alerts, 8-factor signal scorer, trade filter, recommender,
#  and PCR auto-tuner.
# ════════════════════════════════════════════════════════════════

import pandas as pd
from typing import Optional

import state
import config as _cfg
from config import (
    PCR_THRESHOLDS, LOCALIZED_PCR_RANGE,
    ROC_THRESHOLD, ROC_THRESHOLD_SYMBOL, REFRESH_RATE,
    MAX_TRADES_PER_DAY, MIN_SIGNAL_SCORE, MIN_TRADE_GAP_MINS,
    MAX_TRADES_PER_DAY_SYMBOL, MIN_TRADE_GAP_MINS_SYMBOL, SAME_BIAS_OVERRIDE_SCORE,
    NO_TRADE_BEFORE_MINS,
    LOT_SIZE,
)

# Per-symbol OI normalizer: threshold for "strong" intraday net OI addition = full 15 pts.
# Updated for NSE Jan-2026 revised lot sizes:
#   NIFTY 50→65  (+30%): scale 200K → 260K
#   BANKNIFTY 15→30 (+100%): scale 35K → 70K
_OI_NORMALIZER = {
    "NIFTY":       260_000,   # lot 65 (NSE Jan-2026); was 200K at lot 50
    "BANKNIFTY":    70_000,   # lot 30 (NSE Jan-2026); was 35K at lot 15
    "FINNIFTY":     50_000,   # lot unchanged — verify if NSE revised
    "MIDCPNIFTY":   25_000,   # lot unchanged — verify if NSE revised
}
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
            _thr = ROC_THRESHOLD_SYMBOL.get(_cfg.SYMBOL, ROC_THRESHOLD)
            if ce_d > _thr:
                alerts.append(f"CALL BUILDUP  Strike {int(s):,}  +{ce_d:,} OI/{REFRESH_RATE}s")
            if pe_d > _thr:
                alerts.append(f"PUT  BUILDUP  Strike {int(s):,}  +{pe_d:,} OI/{REFRESH_RATE}s")
    state.prev_oi = new_state
    return alerts


# ────────────────────────────────────────────────────────────────
#  Signal Scorer  (8 factors, 0–100 pts)
# ────────────────────────────────────────────────────────────────
#  Factor                              Max pts
#  1  Bias unanimity (proportional)      20
#  2  PCR extremity + trend bonus        20  (15 base + 5 trend bonus)
#  3  Net OI score magnitude             15
#  4  Max pain proximity                 10
#  5  VIX zone                           10
#  6  Time of day                        10
#  7  RoC confirmation                    5
#  8  RSI+VWAP confirmation              15
#  9  Trend persistence (consecutive)    10
#  ─────────────────────────────────────────
#  Raw max                              115  → capped at 100 by min(100,sum)
def score_signal(bias, votes, pcr, net_score, spot, max_pain,
                 vix_str, roc_alerts, tech_signal="NEUTRAL",
                 dealer_vote="NEUTRAL", pcr_trend_bars: int = 0,
                 ml_score_pts: int = 0,
                 regime_score_pts: int = 0) -> tuple:
    """
    Returns (score: int, breakdown: dict, unanimous: bool).

    v5.9: Factor 10 = ML ensemble (sklearn RF/LR, xgboost, mxnet Gluon MLP)
    v5.7 scoring improvements vs original:
      Fix 1  Unanimity   — proportional (votes/total × 20) not cliff-edge
      Fix 2  PCR trend   — rising PCR adds up to 5 confirmation bonus pts
      Fix 3  OI normalizer — per-symbol (BNF 80K, NF 300K)
      Fix 4  Max Pain    — proximity bonus regardless of direction
      Fix 5  VIX         — high VIX on trending days is GOOD, not bad
    """
    pts = {}

    # Fix 1: Proportional unanimity — X out of N active votes matching bias
    active_votes = [v for v in votes if v != "NEUTRAL"]
    total_v      = max(len(active_votes), 1)
    agree_v      = active_votes.count(bias)
    unanimous    = agree_v == total_v and total_v >= 2
    pts["unanimity"] = int((agree_v / total_v) * 20)

    # Fix 2: PCR with trend bonus — rising PCR (bearish signal) = extra pts
    pcr_base  = min(15, int(abs(pcr - 1.0) * 38))
    pcr_trend = min(5, pcr_trend_bars) if (
        (bias == "BEARISH" and pcr_trend_bars > 0)  # PCR rising = bearish confirm
        or (bias == "BULLISH" and pcr_trend_bars < 0)  # PCR falling = bullish confirm
    ) else 0
    pts["pcr"] = min(20, pcr_base + abs(pcr_trend))

    # Fix 3: Per-symbol OI normalizer
    sym        = _cfg.SYMBOL
    normalizer = _OI_NORMALIZER.get(sym, 300_000)
    pts["oi_score"] = min(15, int(abs(net_score) / normalizer * 15))

    # Fix 4: Max pain proximity — far from pain = gravitational pull exists
    pain_dist = abs(spot - max_pain)
    pts["max_pain"] = min(10, int(pain_dist / 100) * 2)

    # Fix 5: VIX — high VIX on trending days = BETTER signal (bigger moves)
    try:
        vix = float(vix_str)
        pts["vix"] = (10 if 18 <= vix <= 26 else   # trending/volatile = ideal
                       8 if 12 <= vix < 18  else   # calm = good
                       4 if vix > 26        else 4) # extreme panic = cautious
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
    pts["roc"]      = roc_bonus
    pts["rsi_vwap"] = (10 if tech_signal == bias else
                       4  if tech_signal == "NEUTRAL" else 0)
    pts["dealer"]   = (5 if dealer_vote == bias else
                       2 if dealer_vote == "NEUTRAL" else 0)

    # Factor 9: Trend persistence — reward signals confirmed across many cycles.
    # Every 10 consecutive cycles with same bias adds 1 pt, up to 10 pts.
    # Prevents single-cycle noise from triggering while rewarding clear trends.
    _consec = getattr(state, "consecutive_bias_cycles", 0)
    pts["trend_persist"] = min(10, (_consec // 10))

    # Factor 10: ML ensemble — sklearn RF/LR, xgboost, mxnet Gluon MLP
    # +0-10 pts when CONFIRM (ensemble confidence ≥ 0.65)
    # −5 pts when CONTRA (ensemble confidence ≤ 0.38 = ML disagrees)
    pts["ml"] = ml_score_pts

    # Factor 11: Market regime (Hurst exponent)
    # TRENDING_UP/DOWN = +10 pts; RANGING/MEAN_REVERTING = 0 pts (no penalty, just no bonus)
    pts["regime"] = min(10, max(0, regime_score_pts))

    return min(100, sum(pts.values())), pts, unanimous


# ────────────────────────────────────────────────────────────────
#  Trade Filter
# ────────────────────────────────────────────────────────────────
def should_take_trade(score: int, bias: str, spot: float = 0, ev_pct: float = None) -> tuple[bool, str]:
    """
    4-gate trade filter. Returns (take: bool, reason: str).

    Gates (in order):
      1. Daily cap           — per-symbol (BNF=5, NF=3)
      2. Minimum score       — 55
      3. Minimum time gap    — per-symbol (BNF=15min, NF=20min)
      4. Same-bias guard     — blocked unless score ≥70 OR spot moved ≥2 strikes
         EXCEPTION: bias reversal (last was BEARISH, now BULLISH) → no gap needed
    """
    import config as _cfg
    sym      = _cfg.SYMBOL
    max_cap  = MAX_TRADES_PER_DAY_SYMBOL.get(sym, MAX_TRADES_PER_DAY)
    min_gap  = MIN_TRADE_GAP_MINS_SYMBOL.get(sym, MIN_TRADE_GAP_MINS)
    sb_score = SAME_BIAS_OVERRIDE_SCORE

    if state.daily_trades_taken >= max_cap:
        return False, f"Daily cap reached ({state.daily_trades_taken}/{max_cap})"
    if score < MIN_SIGNAL_SCORE:
        return False, f"Score {score}/100 < min {MIN_SIGNAL_SCORE}"

    # EV gate: block if the best available strike has negative expected value
    # Implements meta-labeling: direction confirmed, but is there BUYING edge?
    if ev_pct is not None and ev_pct < _cfg.MIN_EV_PCT and bias != "NEUTRAL":
        return False, (
            f"EV gate: best strike EV={ev_pct:.0f}% < min {_cfg.MIN_EV_PCT:.0f}% "
            f"— no statistical buying edge"
        )

    # Opening-session gate — no trades in the first N minutes after market open.
    # Early-morning volatility (09:15-09:35) produces noisy signals.
    if NO_TRADE_BEFORE_MINS > 0:
        from core.market_hours import now_ist as _now_ist
        from datetime import timedelta as _td
        _n = _now_ist()
        if _n.weekday() < 5:   # weekday only (demo runs outside market hours)
            _gate = _n.replace(hour=9, minute=15, second=0, microsecond=0) \
                    + _td(minutes=NO_TRADE_BEFORE_MINS)
            if _n < _gate:
                return False, (
                    f"Opening gate: no trades before "
                    f"{_gate.strftime('%H:%M')} IST "
                    f"({NO_TRADE_BEFORE_MINS}min noise filter)"
                )

    # Check time gap — only skip for genuine directional reversals.
    # NEUTRAL is not a direction: NEUTRAL↔BULLISH/BEARISH is NOT a reversal.
    # Only BULLISH→BEARISH or BEARISH→BULLISH bypasses the gap.
    taken_log = [s for s in state.signal_log if s.taken]
    _DIRECTIONAL = {"BULLISH", "BEARISH"}
    is_reversal = (
        bool(taken_log)
        and taken_log[-1].bias in _DIRECTIONAL
        and bias in _DIRECTIONAL
        and taken_log[-1].bias != bias
    )
    if state.last_trade_time is not None and not is_reversal:
        gap = (now_ist() - state.last_trade_time).seconds // 60
        if gap < min_gap:
            return False, f"Too soon after last trade ({gap}min < {min_gap}min gap)"

    # Same-bias guard — prevent chasing a fading move.
    # Override: (a) high-conviction score >= SAME_BIAS_OVERRIDE_SCORE
    #           (b) spot has moved >= 2 strike steps since last same-bias entry
    if taken_log and taken_log[-1].bias == bias:
        last_spot  = taken_log[-1].spot
        from core.nse_fetcher import strike_step
        _step      = strike_step(sym)
        spot_moved = abs(spot - last_spot) >= _step * 2 if spot and last_spot else False

        if score >= sb_score:
            pass  # high-confidence: allow
        elif spot_moved:
            pass  # price at new level: allow
        else:
            return False, (f"Same bias ({bias}) as last trade — "
                           f"score {score} < {sb_score} and spot hasn't moved >= 2 strikes")

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
