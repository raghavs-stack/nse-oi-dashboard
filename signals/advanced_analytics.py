# ════════════════════════════════════════════════════════════════
#  signals/advanced_analytics.py  v5.6
#  Advanced OI analytics integrated from nse-options-intelligence:
#    - GammaExposure     : net GEX + regime (positive/negative gamma)
#    - SmartMoneyTracker : OI × Volume / IV scoring → institutional strikes
#    - OptionsFlow       : OI change × volume directional flow
#    - OIMomentum        : OI build rate per strike
#    - DealerHedging     : dealer pressure bias (5th vote)
#    - BreakoutDetector  : spot vs OI wall breakout alert
# ════════════════════════════════════════════════════════════════

import pandas as pd


# ── 1. Gamma Exposure ─────────────────────────────────────────────
def calc_gamma_exposure(df: pd.DataFrame, spot: float) -> dict:
    """
    Net Gamma Exposure = sum(CE_OI × (Strike - Spot)) - sum(PE_OI × (Spot - Strike))

    Positive GEX → market makers are long gamma → they BUY dips + SELL rallies
                   → dampens moves, market stays range-bound
    Negative GEX → market makers are short gamma → they SELL dips + BUY rallies
                   → amplifies moves, trending/volatile market expected

    Returns:
        net_gex   : raw GEX value
        regime    : "Positive Gamma" | "Negative Gamma"
        flip_level: strike closest to GEX = 0 (gamma flip point)
    """
    if df.empty:
        return {"net_gex": 0, "regime": "N/A", "flip_level": None}

    d = df.copy()
    d["call_gex"] = d["CE_OI"] * (d["Strike"] - spot)
    d["put_gex"]  = d["PE_OI"] * (spot - d["Strike"])
    d["net_gex"]  = d["call_gex"] - d["put_gex"]

    total_gex = d["net_gex"].sum()
    regime    = "Positive Gamma" if total_gex >= 0 else "Negative Gamma"

    # Gamma flip: strike where cumulative GEX crosses zero
    d_sorted = d.sort_values("Strike")
    d_sorted["cum_gex"] = d_sorted["net_gex"].cumsum()
    flip_row = d_sorted.iloc[(d_sorted["cum_gex"]).abs().argsort()].iloc[0]
    flip_level = int(flip_row["Strike"])

    return {
        "net_gex":    int(total_gex),
        "regime":     regime,
        "flip_level": flip_level,
    }


# ── 2. Smart Money Tracker ────────────────────────────────────────
def calc_smart_money(df: pd.DataFrame) -> dict:
    """
    Smart Money Score = OI_Change × Volume × (1 / (IV + 1))
    Fallback: if Volume is all-zero (pre-open / Shoonya quirk), use raw OI change.

    Returns top 3 call/put strikes with smart money activity, plus readable labels.
    """
    if df.empty:
        return {"calls": [], "puts": [], "dominant": "N/A",
                "call_score": 0, "put_score": 0,
                "call_oi_add": 0, "put_oi_add": 0, "mode": "empty"}

    d = df.copy()
    vol_available = (d["CE_Vol"].sum() + d["PE_Vol"].sum()) > 0

    chg_available = (d["CE_Chg"].abs().sum() + d["PE_Chg"].abs().sum()) > 0

    if vol_available and chg_available:
        # Tier 1: Full formula: OI change × volume × IV discount
        d["smart_call"] = d["CE_Chg"].clip(lower=0) * d["CE_Vol"] * (1 / (d["CE_IV"] + 1))
        d["smart_put"]  = d["PE_Chg"].clip(lower=0) * d["PE_Vol"] * (1 / (d["PE_IV"] + 1))
        mode = "vol+oi"
    elif chg_available:
        # Tier 2: OI additions only (volume not yet streaming)
        d["smart_call"] = d["CE_Chg"].clip(lower=0)
        d["smart_put"]  = d["PE_Chg"].clip(lower=0)
        mode = "oi-chg"
    else:
        # Tier 3: Absolute OI (market open — daychngoi not yet populated)
        d["smart_call"] = d["CE_OI"].clip(lower=0)
        d["smart_put"]  = d["PE_OI"].clip(lower=0)
        mode = "abs-oi"

    # In abs-oi mode use CE_OI/PE_OI for display; otherwise use CE_Chg/PE_Chg
    _oi_col_c = "CE_OI" if mode == "abs-oi" else "CE_Chg"
    _oi_col_p = "PE_OI" if mode == "abs-oi" else "PE_Chg"

    top_calls = d.nlargest(3, "smart_call")[["Strike", _oi_col_c, "smart_call"]].copy()
    top_puts  = d.nlargest(3, "smart_put") [["Strike", _oi_col_p, "smart_put" ]].copy()

    call_score = d["smart_call"].sum()
    put_score  = d["smart_put"].sum()
    dominant   = "CALLS" if call_score > put_score else ("PUTS" if put_score > call_score else "NEUTRAL")

    # Build human-readable lists: [(strike, display_oi_value, score), ...]
    calls_list = [
        (int(r["Strike"]), int(r[_oi_col_c]), int(r["smart_call"]))
        for _, r in top_calls.iterrows()
    ]
    puts_list = [
        (int(r["Strike"]), int(r[_oi_col_p]), int(r["smart_put"]))
        for _, r in top_puts.iterrows()
    ]

    # For display: show OI additions if available, else total absolute OI
    if mode == "abs-oi":
        c_display = int(d["CE_OI"].clip(lower=0).sum())
        p_display = int(d["PE_OI"].clip(lower=0).sum())
        display_label = "Total OI"
    else:
        c_display = int(d["CE_Chg"].clip(lower=0).sum())
        p_display = int(d["PE_Chg"].clip(lower=0).sum())
        display_label = "OI Adds"

    return {
        "calls":        calls_list,
        "puts":         puts_list,
        "dominant":     dominant,
        "call_score":   int(call_score),
        "put_score":    int(put_score),
        "call_oi_add":  c_display,
        "put_oi_add":   p_display,
        "display_label": display_label,
        "mode":         mode,
    }


# ── 3. Options Flow ───────────────────────────────────────────────
def calc_options_flow(df: pd.DataFrame) -> dict:
    """
    Flow Score = OI_Change × Volume  (fresh OI with high volume = real conviction)
    Fallback: use pure OI addition if volume data is not yet available.

    Returns top 3 call/put strikes with their OI change + flow score, net bias.
    """
    if df.empty:
        return {"call_strikes": [], "put_strikes": [], "bias": "N/A",
                "net_flow": 0, "call_flow": 0, "put_flow": 0,
                "top_calls": [], "top_puts": [], "mode": "empty"}

    d = df.copy()
    vol_available = (d["CE_Vol"].sum() + d["PE_Vol"].sum()) > 0

    chg_available = (d["CE_Chg"].abs().sum() + d["PE_Chg"].abs().sum()) > 0

    if vol_available and chg_available:
        # Tier 1: flow = OI change × volume
        d["call_flow"] = d["CE_Chg"].clip(lower=0) * d["CE_Vol"]
        d["put_flow"]  = d["PE_Chg"].clip(lower=0) * d["PE_Vol"]
        mode = "vol+oi"
    elif chg_available:
        # Tier 2: flow = raw OI additions
        d["call_flow"] = d["CE_Chg"].clip(lower=0)
        d["put_flow"]  = d["PE_Chg"].clip(lower=0)
        mode = "oi-chg"
    else:
        # Tier 3: flow = absolute OI (market open, daychngoi not yet populated)
        d["call_flow"] = d["CE_OI"].clip(lower=0)
        d["put_flow"]  = d["PE_OI"].clip(lower=0)
        mode = "abs-oi"

    _fl_oi_c = "CE_OI" if mode == "abs-oi" else "CE_Chg"
    _fl_oi_p = "PE_OI" if mode == "abs-oi" else "PE_Chg"

    top_c = d.nlargest(3, "call_flow")[["Strike", _fl_oi_c, "call_flow"]].copy()
    top_p = d.nlargest(3, "put_flow") [["Strike", _fl_oi_p, "put_flow" ]].copy()

    call_strikes = top_c["Strike"].astype(int).tolist()
    put_strikes  = top_p["Strike"].astype(int).tolist()

    total_call = d["call_flow"].sum()
    total_put  = d["put_flow"].sum()
    net_flow   = int(total_call - total_put)

    # Bias: call flow dominant → calls being written aggressively → BEARISH (resistance)
    if total_call == 0 and total_put == 0:
        bias = "NO DATA"
    elif mode == "abs-oi":
        # In abs-oi mode net_flow = total_call - total_put = CE_OI - PE_OI
        # PCR = PE_OI / CE_OI; > 1.1 = bullish, < 0.9 = bearish
        pcr_ratio = total_put / total_call if total_call > 0 else 1.0
        bias = "BULLISH" if pcr_ratio > 1.1 else ("BEARISH" if pcr_ratio < 0.9 else "NEUTRAL")
    elif net_flow > 0:
        bias = "BEARISH"   # call writers dominant
    elif net_flow < 0:
        bias = "BULLISH"   # put writers dominant
    else:
        bias = "NEUTRAL"

    top_calls_list = [(int(r["Strike"]), int(r[_fl_oi_c]), int(r["call_flow"]))
                      for _, r in top_c.iterrows()]
    top_puts_list  = [(int(r["Strike"]), int(r[_fl_oi_p]), int(r["put_flow"]))
                      for _, r in top_p.iterrows()]

    return {
        "call_strikes": call_strikes,
        "put_strikes":  put_strikes,
        "top_calls":    top_calls_list,
        "top_puts":     top_puts_list,
        "bias":         bias,
        "net_flow":     net_flow,
        "call_flow":    int(total_call),
        "put_flow":     int(total_put),
        "mode":         mode,
    }


# ── 4. OI Momentum ───────────────────────────────────────────────
def calc_oi_momentum(df: pd.DataFrame, spot: float, n: int = 3,
                     symbol: str = "NIFTY") -> dict:
    """
    OI Momentum = OI_Change / (OI + 1)  — build rate, not absolute size

    High momentum at a strike = fresh aggressive positioning regardless of OI size.
    Focuses on ±6 strikes around ATM for relevance (symbol-aware step).
    BUG-06 fix: was hardcoded step=50 / range=300 — wrong for BANKNIFTY (step=100).
    """
    if df.empty:
        return {"call_momentum_strikes": [], "put_momentum_strikes": [],
                "atm_call_mom": 0.0, "atm_put_mom": 0.0}

    d = df.copy()
    _step = 100 if symbol == "BANKNIFTY" else 50
    atm   = round(spot / _step) * _step
    _range = _step * 6    # ±6 strikes: 300 for NIFTY, 600 for BANKNIFTY
    nearby = d[(d["Strike"] >= atm - _range) & (d["Strike"] <= atm + _range)].copy()

    if nearby.empty:
        nearby = d.copy()

    nearby["call_mom"] = nearby["CE_Chg"] / (nearby["CE_OI"] + 1)
    nearby["put_mom"]  = nearby["PE_Chg"] / (nearby["PE_OI"] + 1)

    top_call_strikes = nearby.nlargest(n, "call_mom")["Strike"].astype(int).tolist()
    top_put_strikes  = nearby.nlargest(n, "put_mom") ["Strike"].astype(int).tolist()

    # ATM momentum
    atm_row      = nearby.iloc[(nearby["Strike"] - spot).abs().argsort()].iloc[0]
    atm_call_mom = round(float(atm_row["call_mom"]), 4)
    atm_put_mom  = round(float(atm_row["put_mom"]),  4)

    return {
        "call_momentum_strikes": top_call_strikes,
        "put_momentum_strikes":  top_put_strikes,
        "atm_call_mom":          atm_call_mom,
        "atm_put_mom":           atm_put_mom,
    }


# ── 5. Dealer Hedging ─────────────────────────────────────────────
def calc_dealer_hedging(df: pd.DataFrame, spot: float) -> dict:
    """
    Dealer Pressure = sum(CE_OI × (Spot - Strike)) - sum(PE_OI × (Strike - Spot))

    When dealers sell calls, they buy futures to delta-hedge → upside pressure
    When dealers sell puts, they sell futures to delta-hedge → downside pressure

    Positive → Dealer Upside Hedge (bullish undercurrent)
    Negative → Dealer Downside Hedge (bearish undercurrent)

    Use as 5th vote in bias scoring.
    """
    if df.empty:
        return {"pressure": 0, "bias": "NEUTRAL", "vote": "NEUTRAL"}

    d = df.copy()
    d["call_pressure"] = d["CE_OI"] * (spot - d["Strike"])
    d["put_pressure"]  = d["PE_OI"] * (d["Strike"] - spot)
    net = d["call_pressure"].sum() - d["put_pressure"].sum()

    if net > 0:
        bias = "Upside Hedge"
        vote = "BULLISH"
    elif net < 0:
        bias = "Downside Hedge"
        vote = "BEARISH"
    else:
        bias = "Neutral"
        vote = "NEUTRAL"

    return {"pressure": int(net), "bias": bias, "vote": vote}


# ── 6. Breakout Detector ──────────────────────────────────────────
def calc_breakout(df: pd.DataFrame, spot: float) -> dict:
    """
    Detects if spot has broken above the max CE_OI wall (resistance)
    or below the max PE_OI wall (support).

    Breakout above resistance = shorts being squeezed → bullish momentum
    Breakdown below support   = puts being triggered → bearish momentum
    """
    if df.empty:
        return {"resistance": None, "support": None,
                "signal": None, "breakout": False}

    resistance = int(df.loc[df["CE_OI"].idxmax(), "Strike"])
    support    = int(df.loc[df["PE_OI"].idxmax(), "Strike"])

    signal   = None
    breakout = False
    if spot > resistance:
        signal   = "⚡ Upside Breakout"
        breakout = True
    elif spot < support:
        signal   = "⚡ Downside Breakdown"
        breakout = True

    return {
        "resistance": resistance,
        "support":    support,
        "signal":     signal,
        "breakout":   breakout,
    }


# ── Composite runner ─────────────────────────────────────────────
def run_advanced_analytics(df: pd.DataFrame, spot: float, symbol: str = "NIFTY") -> dict:
    """Run all advanced analytics in one call. Returns combined dict."""
    return {
        "gamma":       calc_gamma_exposure(df, spot),
        "smart_money": calc_smart_money(df),
        "flow":        calc_options_flow(df),
        "momentum":    calc_oi_momentum(df, spot, symbol=symbol),
        "dealer":      calc_dealer_hedging(df, spot),
        "breakout":    calc_breakout(df, spot),
    }
