# ════════════════════════════════════════════════════════════════
#  signals/options_math.py  — v5.10
#
#  Institutional-grade options analytics for HIGH-PROBABILITY BUYING
#  ──────────────────────────────────────────────────────────────────
#  Based on:
#    • Black-Scholes-Merton framework
#    • Volatility Cone (Brenner & Galai 1989, used at every major HF)
#    • Expected Value framework (Marcos López de Prado: "Advances in
#      Financial Machine Learning" — Chapter 3: meta-labeling)
#    • IV vs Realized Vol mispricing (core Citadel vol-arb concept)
#
#  Key principle: ONLY buy options when:
#    1. IV < Realized Vol (options are CHEAP vs what they should cost)
#    2. Probability ITM ≥ 40%  (delta ≥ 0.40 for CE, ≤ -0.40 for PE)
#    3. Expected Value > 0  (positive edge after all scenarios)
#    4. Theta/Vega ratio < 0.3  (don't pay for time, pay for move)
#
#  Exposed functions:
#    bs_greeks(spot, strike, tte, iv_pct, opt_type, r) → GreeksResult
#    calc_ev(premium, prob_itm, target_mult, sl_mult) → float
#    volatility_cone(iv_history_df, current_iv, spot_series) → dict
#    rank_strikes(df, spot, bias, tte, iv_history_df, lot_size) → list[StrikeRank]
#    option_buy_gate(greeks, ev, regime) → (bool, str)
# ════════════════════════════════════════════════════════════════

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional, List
import numpy as np
import pandas as pd
from scipy.stats import norm


# ── Risk-free rate (91-day Indian T-Bill, Mar-2026) ──────────────
_R_FREE = 0.065


# ════════════════════════════════════════════════════════════════
#  Data classes
# ════════════════════════════════════════════════════════════════
@dataclass
class GreeksResult:
    """Full first-order & second-order Greeks for one option."""
    strike:    float
    opt_type:  str       # "CE" | "PE"
    premium:   float
    iv_pct:    float     # ATM IV in percent (e.g. 14.5)
    delta:     float     # CE: 0–1 | PE: -1–0
    gamma:     float     # same for CE and PE
    theta:     float     # daily theta (negative = time decay cost)
    vega:      float     # per 1% IV change
    prob_itm:  float     # probability option expires ITM  (0–1)
    d1:        float
    d2:        float
    ev:        float     = 0.0   # expected value at entry
    tv_ratio:  float     = 0.0   # |theta / vega| — lower = better for buyers
    moneyness: float     = 0.0   # (spot - strike) / strike for CE, reverse for PE


@dataclass
class StrikeRank:
    """One recommended strike with full analytics."""
    label:     str
    strike:    int
    opt_type:  str
    premium:   float
    greeks:    GreeksResult
    ev:        float
    ev_pct:    float       # ev as % of premium invested
    lot_cost:  float
    target:    float
    sl:        float
    rr:        str
    reason:    str
    buy_grade: str         # A / B / C / SKIP
    grade_reason: str      = ""


# ════════════════════════════════════════════════════════════════
#  1.  Black-Scholes Greeks
# ════════════════════════════════════════════════════════════════
def bs_greeks(
    spot:     float,
    strike:   float,
    tte:      float,         # time to expiry in YEARS (e.g. 7/365)
    iv_pct:   float,         # implied vol in PERCENT (e.g. 14.5)
    opt_type: str,           # "CE" | "PE"
    r:        float = _R_FREE,
    premium:  float = 0.0,
) -> GreeksResult:
    """
    Compute full BSM Greeks for an NSE index option.

    Assumptions:
      • European-style (NSE index options: correct)
      • Continuous dividend yield = 0 (conservative for index)
      • r = 6.5% risk-free (Indian 91-day T-Bill, Mar-2026)

    Returns GreeksResult with delta, gamma, theta, vega, prob_itm.
    Returns zeroed GreeksResult on bad inputs (avoids crashes in live feed).
    """
    _zero = GreeksResult(
        strike=strike, opt_type=opt_type, premium=premium,
        iv_pct=iv_pct, delta=0.0, gamma=0.0, theta=0.0,
        vega=0.0, prob_itm=0.0, d1=0.0, d2=0.0,
    )
    try:
        if spot <= 0 or strike <= 0 or tte <= 0 or iv_pct <= 0:
            return _zero
        sigma = iv_pct / 100.0
        sqt   = math.sqrt(tte)

        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * tte) / (sigma * sqt)
        d2 = d1 - sigma * sqt

        nd1  = float(norm.cdf(d1))
        nd2  = float(norm.cdf(d2))
        nd1_ = float(norm.pdf(d1))   # standard normal PDF at d1

        if opt_type == "CE":
            delta    =  nd1
            prob_itm =  nd2           # risk-neutral P(S_T > K)
        else:
            delta    =  nd1 - 1       # = -N(-d1)
            prob_itm =  1 - nd2       # risk-neutral P(S_T < K)

        gamma = nd1_ / (spot * sigma * sqt)

        # Theta: daily decay (divide annual by 365)
        theta_annual = (
            -(spot * nd1_ * sigma) / (2 * sqt)
            - r * strike * math.exp(-r * tte) * (nd2 if opt_type == "CE" else (1 - nd2))
        )
        theta = theta_annual / 365.0   # per calendar day

        # Vega: sensitivity to 1% change in IV
        vega  = spot * nd1_ * sqt / 100.0

        # Moneyness: positive = ITM for CE, negative = OTM
        moneyness = (spot - strike) / strike if opt_type == "CE" else (strike - spot) / strike

        # Theta/Vega ratio — buyer wants this LOW
        tv_ratio = abs(theta / vega) if vega != 0 else 99.9

        return GreeksResult(
            strike=strike, opt_type=opt_type, premium=premium,
            iv_pct=iv_pct, delta=delta, gamma=gamma, theta=theta,
            vega=vega, prob_itm=prob_itm, d1=d1, d2=d2,
            moneyness=moneyness, tv_ratio=tv_ratio,
        )
    except Exception:
        return _zero


# ════════════════════════════════════════════════════════════════
#  2.  Expected Value calculator
# ════════════════════════════════════════════════════════════════
def calc_ev(
    premium:      float,
    prob_itm:     float,    # P(option expires ITM) — use bs_greeks.prob_itm
    target_mult:  float = 2.0,   # win = 2× premium (100% gain)
    sl_mult:      float = 0.5,   # loss = 0.5× premium (50% loss)
) -> float:
    """
    Single-period Expected Value for a long option position.

    EV = P(win) × (target_premium - premium)
       - P(loss) × (premium - sl_premium)

    Uses prob_itm as the natural win probability (options expire ITM).
    Adjusts for realistic partial wins: not every ITM option hits 2×.

    Returns EV in rupees per lot-unit (multiply by lot size for total).
    Returns negative EV when the trade has no statistical edge.

    Key threshold: EV > 0.15 × premium = positive edge (15% return expectancy).
    """
    if premium <= 0:
        return 0.0
    p_win  = min(0.95, max(0.05, prob_itm))
    p_loss = 1.0 - p_win
    win    = premium * (target_mult - 1)   # gain if target hit
    loss   = premium * (1 - sl_mult)       # loss if SL hit
    return round(p_win * win - p_loss * loss, 2)


def calc_ev_pct(ev: float, premium: float) -> float:
    """EV as percentage of capital deployed."""
    return round(ev / premium * 100, 1) if premium > 0 else 0.0


# ════════════════════════════════════════════════════════════════
#  3.  Volatility Cone
# ════════════════════════════════════════════════════════════════
def realized_vol(spot_series: list, window: int) -> Optional[float]:
    """
    Annualized realized volatility over `window` periods.
    Uses log-return standard deviation × √(252 × 375/window_in_mins).
    For 35-second cycles: trading-day equivalent annualization.

    Returns None if insufficient data.
    """
    if len(spot_series) < window + 1:
        return None
    prices = spot_series[-(window + 1):]
    log_rets = [math.log(prices[i] / prices[i-1]) for i in range(1, len(prices))
                if prices[i] > 0 and prices[i-1] > 0]
    if len(log_rets) < 5:
        return None
    std_per_cycle = float(np.std(log_rets, ddof=1))
    # Annualize: NSE trades 375 min/day × 60/35 ≈ 643 cycles/day × 252 days
    cycles_per_year = 643 * 252
    return round(std_per_cycle * math.sqrt(cycles_per_year) * 100, 2)


def volatility_cone(
    iv_history: pd.DataFrame,   # IVHistory.history (cols: Date, ATM_IV)
    current_iv:  float,
    spot_series: list,           # rolling spot prices from state
) -> dict:
    """
    Volatility Cone: compare current IV to historical realized vol percentiles.

    A core Citadel concept: buy options when IV is in the BOTTOM QUARTILE
    of its historical range relative to what volatility actually was.

    Returns:
      rv_20:        current realized vol (20-cycle rolling)
      rv_60:        current realized vol (60-cycle rolling)
      iv_rv_ratio:  current_iv / rv_20 (< 1.0 = options CHEAP vs realized)
      cone_pct:     current IV as percentile in 252-day IV history
      signal:       "CHEAP" | "FAIR" | "EXPENSIVE"
      buy_edge:     True if IV < realized vol  (the primary buying edge)
    """
    rv20 = realized_vol(spot_series, 20)
    rv60 = realized_vol(spot_series, 60)

    iv_rv_ratio = None
    if rv20 and rv20 > 0:
        iv_rv_ratio = round(current_iv / rv20, 3)

    cone_pct = None
    if not iv_history.empty and "ATM_IV" in iv_history.columns:
        hist_ivs = iv_history["ATM_IV"].dropna().values
        if len(hist_ivs) >= 20:
            cone_pct = round(float(np.mean(hist_ivs <= current_iv)) * 100, 1)

    # Signal classification
    if iv_rv_ratio is not None:
        if iv_rv_ratio < 0.85:
            signal = "CHEAP"       # IV << Realized — strong buying edge
        elif iv_rv_ratio < 1.15:
            signal = "FAIR"        # IV ≈ Realized — neutral
        else:
            signal = "EXPENSIVE"   # IV >> Realized — sell / spread
    elif cone_pct is not None:
        if cone_pct < 30:
            signal = "CHEAP"
        elif cone_pct > 70:
            signal = "EXPENSIVE"
        else:
            signal = "FAIR"
    else:
        signal = "FAIR"

    buy_edge = (signal == "CHEAP") or (iv_rv_ratio is not None and iv_rv_ratio < 1.0)

    return {
        "rv_20":       rv20,
        "rv_60":       rv60,
        "iv_rv_ratio": iv_rv_ratio,
        "cone_pct":    cone_pct,
        "signal":      signal,
        "buy_edge":    buy_edge,
    }


# ════════════════════════════════════════════════════════════════
#  4.  Liquidity scorer
# ════════════════════════════════════════════════════════════════
def liquidity_score(oi: float, volume: float, premium: float) -> int:
    """
    Score 0–100 for how liquid a strike is.
    Combines OI depth, today's volume, and premium size.
    Prevents entering illiquid options with wide spreads.
    """
    score = 0
    if oi > 50_000:   score += 30
    elif oi > 10_000: score += 15
    if volume > 5_000:   score += 30
    elif volume > 1_000: score += 15
    if premium >= 20: score += 20
    elif premium >= 8: score += 10
    if premium <= 500: score += 20   # not too deep ITM / expensive
    return min(100, score)


# ════════════════════════════════════════════════════════════════
#  5.  Strike ranker — the master entry-point for trade recs
# ════════════════════════════════════════════════════════════════
def rank_strikes(
    df:           pd.DataFrame,
    spot:         float,
    bias:         str,
    tte:          float,
    current_iv:   float,       # ATM IV in pct
    iv_history:   pd.DataFrame,
    lot_size:     int,
    spot_series:  list,
    symbol:       str = "NIFTY",
    n_strikes:    int = 3,
) -> list:
    """
    Rank the top N buyable strikes using Greeks, EV, and liquidity.

    Selection criteria (institutional option buying):
      1.  delta ≥ 0.30  (meaningful directional exposure)
      2.  prob_itm ≥ 0.35
      3.  EV > 0  (positive expected value)
      4.  theta/vega ratio < 0.5  (decay doesn't eat the move)
      5.  liquidity ≥ 40

    Grades:
      A = prob_itm ≥ 0.42 AND EV_pct ≥ 20% AND vol = CHEAP
      B = prob_itm ≥ 0.35 AND EV_pct ≥ 10%
      C = basic minimum pass
      SKIP = failed gate

    Returns list of StrikeRank (best first).
    """
    from core.nse_fetcher import nearest_strike, strike_step
    step = strike_step(symbol)
    atm  = nearest_strike(spot, symbol)
    vcone = volatility_cone(iv_history, current_iv, spot_series)

    candidates = []
    if bias == "BULLISH":
        strikes_to_check = [atm - step, atm, atm + step, atm + 2 * step]
        opt_type = "CE"
        labels   = ["1-ITM (Deep)", "ATM", "1-OTM (Moderate)", "2-OTM (Aggressive)"]
    elif bias == "BEARISH":
        strikes_to_check = [atm + step, atm, atm - step, atm - 2 * step]
        opt_type = "PE"
        labels   = ["1-ITM (Deep)", "ATM", "1-OTM (Moderate)", "2-OTM (Aggressive)"]
    else:
        # Neutral: ATM straddle components
        strikes_to_check = [atm, atm, atm - step, atm + step]
        opt_types = ["CE", "PE", "PE", "CE"]
        labels    = ["ATM CE", "ATM PE", "1-OTM PE", "1-OTM CE"]

    if bias == "NEUTRAL":
        strike_configs = list(zip(strikes_to_check, opt_types, labels))
    else:
        strike_configs = [(s, opt_type, l) for s, l in zip(strikes_to_check, labels)]

    for strike, otype, label in strike_configs:
        row = df[df["Strike"] == float(strike)]
        if row.empty:
            continue

        col_ltp  = f"{otype}_LTP"
        col_oi   = f"{otype}_OI"
        col_vol  = f"{otype}_Vol"
        col_iv   = f"{otype}_IV"

        prem = float(row[col_ltp].iloc[0]) if col_ltp in row.columns else 0.0
        oi   = float(row[col_oi].iloc[0])  if col_oi  in row.columns else 0.0
        vol  = float(row[col_vol].iloc[0]) if col_vol  in row.columns else 0.0
        iv   = float(row[col_iv].iloc[0])  if col_iv   in row.columns else current_iv

        if prem < 1.0:
            prem = max(2.0, abs(strike - spot) * 0.003 + 5)
        if iv <= 0:
            iv = current_iv or 15.0

        g = bs_greeks(spot, strike, tte, iv, otype, premium=prem)
        ev = calc_ev(prem, g.prob_itm, target_mult=2.0, sl_mult=0.5)
        ev_pct = calc_ev_pct(ev, prem)
        liq = liquidity_score(oi, vol, prem)
        g.ev = ev

        # Grade
        is_cheap = vcone.get("signal") == "CHEAP"
        if g.prob_itm >= 0.42 and ev_pct >= 20 and is_cheap and liq >= 40:
            grade = "A"
            grade_reason = f"prob={g.prob_itm:.0%} ev={ev_pct:.0f}% IV=CHEAP liq={liq}"
        elif g.prob_itm >= 0.35 and ev_pct >= 10 and liq >= 30:
            grade = "B"
            grade_reason = f"prob={g.prob_itm:.0%} ev={ev_pct:.0f}% liq={liq}"
        elif g.prob_itm >= 0.28 and ev > 0 and liq >= 20:
            grade = "C"
            grade_reason = f"prob={g.prob_itm:.0%} ev={ev_pct:.0f}% marginal"
        else:
            grade = "SKIP"
            grade_reason = (f"prob={g.prob_itm:.0%} ev={ev_pct:.0f}% "
                            f"liq={liq} — below minimum thresholds")

        # Trade geometry
        target = round(prem * 2.00, 1)
        sl     = round(prem * 0.50, 1)
        lot_cost = round(prem * lot_size, 0)

        reason = (f"Δ={g.delta:+.2f}  Γ={g.gamma:.4f}  "
                  f"θ={g.theta:.2f}/d  ν={g.vega:.2f}  "
                  f"P(ITM)={g.prob_itm:.0%}  EV={ev_pct:+.0f}%")

        candidates.append(StrikeRank(
            label=label, strike=int(strike), opt_type=otype,
            premium=prem, greeks=g, ev=ev, ev_pct=ev_pct,
            lot_cost=lot_cost, target=target, sl=sl,
            rr="1:2", reason=reason, buy_grade=grade,
            grade_reason=grade_reason,
        ))

    # Sort: A > B > C > SKIP; within grade: higher EV first
    grade_order = {"A": 0, "B": 1, "C": 2, "SKIP": 3}
    candidates.sort(key=lambda x: (grade_order.get(x.buy_grade, 4), -x.ev_pct))
    return candidates[:n_strikes]


# ════════════════════════════════════════════════════════════════
#  6.  Option-buy gate  (meta-labeling gate)
# ════════════════════════════════════════════════════════════════
def option_buy_gate(
    top_strike: "StrikeRank | None",
    vcone:       dict,
    regime:      str,           # from regime_detector
    dte:         int,
) -> tuple[bool, str]:
    """
    Final meta-labeling gate: should we BUY an option?

    Implements Marcos López de Prado's concept of meta-labeling:
    FIRST a direction model says BULLISH/BEARISH.
    THEN this gate asks: "Is this a good time to BUY an option on that?"

    Gates (all must pass):
      1.  Best strike must be grade A or B
      2.  EV > 0 on best strike
      3.  Regime is not RANGING or MEAN_REVERTING (options decay in range)
      4.  DTE ≥ 2  (avoid expiry day gamma trap)

    Returns (pass: bool, reason: str).
    """
    if top_strike is None:
        return False, "No valid strike found"
    if top_strike.buy_grade == "SKIP":
        return False, f"Best strike is SKIP: {top_strike.grade_reason}"
    if top_strike.ev <= 0:
        return False, f"Negative EV ({top_strike.ev:.1f}): no statistical edge"
    if regime in ("RANGING", "MEAN_REVERTING"):
        return False, f"Market regime={regime}: directional options decay in range"
    if dte < 2:
        return False, f"DTE={dte} < 2: expiry gamma trap — skip"
    return True, f"PASS: grade={top_strike.buy_grade} ev={top_strike.ev_pct:+.0f}% regime={regime}"
