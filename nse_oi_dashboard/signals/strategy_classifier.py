# ════════════════════════════════════════════════════════════════
#  signals/strategy_classifier.py  v5.7
#
#  Classifies each cycle into a specific STRATEGY:
#    BUYING strategies  → enter when IV is cheap or trend is strong
#    SELLING strategies → enter when IV is expensive or market is ranging
#
#  Strategy matrix (IV Regime × Directional Bias):
#
#            BULLISH       NEUTRAL       BEARISH
#  LOW IV    LONG CALL     LONG STRADDLE LONG PUT
#  NORMAL    BULL SPREAD   IRON CONDOR   BEAR SPREAD
#  HIGH IV   BULL SPREAD   SHORT STRANGLE BEAR SPREAD
#                          SHORT STRADDLE
#
#  Additionally: day-quality gate + momentum confirmation
# ════════════════════════════════════════════════════════════════

from __future__ import annotations
from typing import Optional


# ── IV Regime thresholds ──────────────────────────────────────────
IV_LOW_IVR    = 30    # IVR below this = cheap premium → buy
IV_HIGH_IVR   = 55    # IVR above this = expensive premium → sell
IV_LOW_IVP    = 35
IV_HIGH_IVP   = 65


def get_iv_regime(ivr: Optional[float], ivp: Optional[float]) -> str:
    """
    Returns: "LOW IV" | "NORMAL IV" | "HIGH IV"
    Uses IVR primarily, IVP as confirmation.
    """
    if ivr is None:
        return "NORMAL IV"
    if ivr <= IV_LOW_IVR:
        return "LOW IV"
    if ivr >= IV_HIGH_IVR:
        return "HIGH IV"
    return "NORMAL IV"


# ── Strategy definitions ──────────────────────────────────────────
STRATEGIES = {
    # ── BUYING STRATEGIES ──────────────────────────────────────
    "LONG_CALL": {
        "type":       "BUY",
        "bias":       "BULLISH",
        "iv_regime":  ["LOW IV"],
        "name":       "Long Call",
        "risk":       "LIMITED (premium paid)",
        "reward":     "UNLIMITED",
        "ideal_dte":  "7–21 days",
        "entry":      "Buy ATM or 1-OTM call",
        "exit_win":   "100% premium gain (2x entry)",
        "exit_loss":  "50% premium loss (0.5x entry)",
        "note":       "Best when trend is strong AND IV is cheap. Hold through momentum.",
    },
    "LONG_PUT": {
        "type":       "BUY",
        "bias":       "BEARISH",
        "iv_regime":  ["LOW IV"],
        "name":       "Long Put",
        "risk":       "LIMITED (premium paid)",
        "reward":     "UNLIMITED downside",
        "ideal_dte":  "7–21 days",
        "entry":      "Buy ATM or 1-OTM put",
        "exit_win":   "100% premium gain",
        "exit_loss":  "50% premium loss",
        "note":       "Best on bearish reversals when IV not yet spiked.",
    },
    "LONG_STRADDLE": {
        "type":       "BUY",
        "bias":       "NEUTRAL",
        "iv_regime":  ["LOW IV"],
        "name":       "Long Straddle",
        "risk":       "LIMITED (both premiums)",
        "reward":     "UNLIMITED both sides",
        "ideal_dte":  "7–14 days",
        "entry":      "Buy ATM call + ATM put",
        "exit_win":   "Move > combined premium",
        "exit_loss":  "50% of total premium",
        "note":       "Pre-event play. Only when IV is historically low.",
    },
    # ── BALANCED STRATEGIES ─────────────────────────────────────
    "BULL_SPREAD": {
        "type":       "SPREAD",
        "bias":       "BULLISH",
        "iv_regime":  ["NORMAL IV", "HIGH IV"],
        "name":       "Bull Call Spread",
        "risk":       "LIMITED (net debit)",
        "reward":     "LIMITED (spread width)",
        "ideal_dte":  "4–14 days",
        "entry":      "Buy ATM call + Sell 1-OTM call",
        "exit_win":   "Spot above short strike at expiry",
        "exit_loss":  "Both legs expire worthless",
        "note":       "Reduce cost in high IV. Better R:R than naked long call.",
    },
    "BEAR_SPREAD": {
        "type":       "SPREAD",
        "bias":       "BEARISH",
        "iv_regime":  ["NORMAL IV", "HIGH IV"],
        "name":       "Bear Put Spread",
        "risk":       "LIMITED (net debit)",
        "reward":     "LIMITED (spread width)",
        "ideal_dte":  "4–14 days",
        "entry":      "Buy ATM put + Sell 1-OTM put",
        "exit_win":   "Spot below short strike at expiry",
        "exit_loss":  "Both legs expire worthless",
        "note":       "Reduce cost in high IV. Defined risk bearish play.",
    },
    # ── SELLING STRATEGIES ──────────────────────────────────────
    "SHORT_STRANGLE": {
        "type":       "SELL",
        "bias":       "NEUTRAL",
        "iv_regime":  ["HIGH IV"],
        "name":       "Short Strangle",
        "risk":       "UNLIMITED",
        "reward":     "LIMITED (premium collected)",
        "ideal_dte":  "7–21 days",
        "entry":      "Sell OTM call (2%) + Sell OTM put (2%) from spot",
        "exit_win":   "Both expire OTM. Keep full premium.",
        "exit_loss":  "Adjust/exit if spot breaches either strike",
        "note":       "Best on HIGH IV range-bound days. Delta-neutral initially.",
    },
    "SHORT_STRADDLE": {
        "type":       "SELL",
        "bias":       "NEUTRAL",
        "iv_regime":  ["HIGH IV"],
        "name":       "Short Straddle",
        "risk":       "UNLIMITED",
        "reward":     "LIMITED (both premiums)",
        "ideal_dte":  "2–5 days",
        "entry":      "Sell ATM call + Sell ATM put",
        "exit_win":   "Spot stays within breakevens",
        "exit_loss":  "Spot moves > collected premium",
        "note":       "Near-expiry only. Exit if spot moves 1% intraday.",
    },
    "IRON_CONDOR": {
        "type":       "SELL",
        "bias":       "NEUTRAL",
        "iv_regime":  ["NORMAL IV", "HIGH IV"],
        "name":       "Iron Condor",
        "risk":       "LIMITED (spread width - premium)",
        "reward":     "LIMITED (net premium)",
        "ideal_dte":  "7–21 days",
        "entry":      "Bull put spread + Bear call spread OTM",
        "exit_win":   "Spot stays between short strikes",
        "exit_loss":  "Spot breaches either inner strike",
        "note":       "Defined risk. Best for range-bound high-IV sessions.",
    },
}


# ── Main classifier ───────────────────────────────────────────────
def classify_strategy(
    bias:         str,
    ivr:          Optional[float],
    ivp:          Optional[float],
    atm_iv:       Optional[float],
    score:        int,
    days_to_exp:  int,
    day_quality:  int,
    pcr_div_sig:  str = "ALIGNED",
) -> dict:
    """
    Returns the recommended strategy and specific trade recommendations.

    bias:         BULLISH | BEARISH | NEUTRAL
    ivr:          IV Rank (0–100)
    ivp:          IV Percentile (0–100)
    score:        composite signal score (0–100)
    days_to_exp:  days to nearest expiry
    day_quality:  composite day quality score (0–100)
    pcr_div_sig:  from PCRSeries (ALIGNED | BOUNCE LIKELY | FADE RALLY)
    """
    iv_regime  = get_iv_regime(ivr, ivp)
    strategies = []

    # Map bias + IV regime → primary strategies
    if bias == "BULLISH":
        if iv_regime == "LOW IV":
            strategies = ["LONG_CALL"]
        elif iv_regime == "HIGH IV":
            strategies = ["BULL_SPREAD"]
        else:
            strategies = ["LONG_CALL", "BULL_SPREAD"]

    elif bias == "BEARISH":
        if iv_regime == "LOW IV":
            strategies = ["LONG_PUT"]
        elif iv_regime == "HIGH IV":
            strategies = ["BEAR_SPREAD"]
        else:
            strategies = ["LONG_PUT", "BEAR_SPREAD"]

    else:  # NEUTRAL
        if iv_regime == "HIGH IV":
            if days_to_exp <= 3:
                strategies = ["SHORT_STRADDLE"]
            elif days_to_exp <= 10:
                strategies = ["SHORT_STRANGLE"]
            else:
                strategies = ["IRON_CONDOR"]
        elif iv_regime == "LOW IV":
            strategies = ["LONG_STRADDLE"]
        else:
            strategies = ["IRON_CONDOR"]

    # Override with divergence signal
    if pcr_div_sig == "BOUNCE LIKELY" and bias != "BULLISH":
        strategies.insert(0, "LONG_CALL")   # contrarian bounce
    elif pcr_div_sig == "FADE RALLY" and bias == "BULLISH":
        # Fade the bullishness with a spread instead of naked long
        strategies = ["BULL_SPREAD"] + strategies

    primary = strategies[0] if strategies else "IRON_CONDOR"
    strat   = STRATEGIES.get(primary, STRATEGIES["IRON_CONDOR"])

    # Gate: is the overall environment trade-worthy?
    tradeable = (
        score        >= 50 and
        day_quality  >= 50 and
        days_to_exp  > 1
    )

    # Conviction level
    conviction = "LOW"
    if score >= 70 and day_quality >= 65 and iv_regime in ["LOW IV", "HIGH IV"]:
        conviction = "HIGH"
    elif score >= 55 and day_quality >= 55:
        conviction = "MEDIUM"

    # Position sizing guidance based on conviction
    sizing = {
        "HIGH":   {"lots": 2, "note": "Full size. All conditions aligned."},
        "MEDIUM": {"lots": 1, "note": "Half size. Partial confirmation."},
        "LOW":    {"lots": 0, "note": "Skip or paper-trade only."},
    }

    return {
        "iv_regime":   iv_regime,
        "strategy":    primary,
        "strategy_name": strat["name"],
        "strategy_type": strat["type"],     # BUY | SELL | SPREAD
        "entry":       strat["entry"],
        "exit_win":    strat["exit_win"],
        "exit_loss":   strat["exit_loss"],
        "ideal_dte":   strat["ideal_dte"],
        "risk":        strat["risk"],
        "reward":      strat["reward"],
        "note":        strat["note"],
        "conviction":  conviction,
        "sizing":      sizing[conviction],
        "tradeable":   tradeable,
        "all_strategies": strategies,
    }


def format_strategy_panel(sc: dict, ivr, ivp, atm_iv) -> list[str]:
    """Format for terminal display."""
    lines = []
    a = lines.append
    W = 68
    a("─" * W)
    a(f"  STRATEGY CLASSIFIER  [{sc['iv_regime']}]  →  {sc['strategy_name']}")
    a("─" * W)
    a(f"  Type: {sc['strategy_type']:<10}  Conviction: {sc['conviction']:<8}  "
      f"Lots: {sc['sizing']['lots']}")
    a(f"  Entry:     {sc['entry']}")
    a(f"  Win exit:  {sc['exit_win']}")
    a(f"  Loss exit: {sc['exit_loss']}")
    a(f"  DTE:       {sc['ideal_dte']}   |  Risk: {sc['risk']}")
    a(f"  Note: {sc['note']}")
    if ivr is not None:
        a(f"  IVR: {ivr:.1f}  |  IVP: {ivp:.1f if ivp else 'N/A'}  |  ATM IV: {atm_iv:.2f if atm_iv else 'N/A'}%")
    a("─" * W)
    return lines
