#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════
#  NSE Live OI Dashboard  v5.3
#  Entry point — terminal mode (default) or Tkinter GUI (--gui)
#
#  Usage:
#    python main.py             # terminal / Colab
#    python main.py --gui       # Tkinter GUI (local PC only)
#
#  pip install pandas matplotlib yfinance NorenRestApiPy pyotp streamlit
# ════════════════════════════════════════════════════════════════

import sys, os, math, random, time

# ── Auto-install in Colab ─────────────────────────────────────────
try:
    import google.colab
    IN_COLAB = True
    os.system("pip install -q yfinance requests pandas matplotlib")
except ImportError:
    IN_COLAB = False

import pandas as pd

import state
import config
from config import (
    SYMBOL, LOT_SIZE, REFRESH_RATE, LOG_FILE,
    MAX_TRADES_PER_DAY, LOCALIZED_PCR_RANGE, RSI_PERIOD,
)
from core.market_hours import is_market_open, is_eod, next_open_str, now_ist
from core.nse_fetcher  import (
    create_session, fetch_chain, fetch_vix,
    build_df, demo_data, nearest_strike, strike_step
)
from signals.indicators  import StrategyEngine
from signals.oi_analytics import (
    calc_max_pain, calc_localized_pcr, generate_pcr_signal,
    compute_roc_alerts, score_signal,
    should_take_trade, register_trade_taken,
    recommend_strikes, auto_tune,
)
from signals.iv_analytics import (
    calc_atm_iv, calc_iv_skew, IVTracker, IVHistory,
    interpret_iv,
)
from signals.advanced_analytics import run_advanced_analytics
from signals.multi_pcr import PCRSeries, classify_expiries, calc_pcr_for_expiry
from signals.trade_calendar import get_day_quality
from signals.strategy_classifier import classify_strategy, format_strategy_panel
from alerts.telegram_alerts import (
    init_telegram, send_signal, send_breakout, send_roc_alert, send_eod_summary,
)
from display.terminal import render
from backtest.eod_backtest import Signal, run_eod_backtest
import json

# ── Singleton IV objects (persist across cycles) ─────────────────
iv_tracker  = IVTracker()
iv_history  = IVHistory()
pcr_series  = PCRSeries()          # Weekly + Monthly PCR with EMA/VWAP


# ════════════════════════════════════════════════════════════════
#  Per-symbol state container (for --both mode)
# ════════════════════════════════════════════════════════════════
class SymbolEngine:
    """Holds all per-symbol singletons so two symbols can run in one process."""
    def __init__(self, symbol: str):
        self.symbol      = symbol
        # NSE revised lot sizes from Jan-2026 expiry cycle
        self.lot_size    = {"NIFTY": 65, "BANKNIFTY": 30,
                            "FINNIFTY": 40, "MIDCPNIFTY": 75}.get(symbol, 65)
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y%m%d")
        self.log_file    = f"{symbol}_OI_{today}.csv"
        self.iv_hist_file= f"{symbol}_iv_history.csv"
        self.plot_file   = f"{symbol}_chart.png"
        self.iv_tracker  = IVTracker()
        self.iv_history  = IVHistory(filepath=self.iv_hist_file)
        self.pcr_series  = PCRSeries()
        self.signal_log  = []
        self.pcr_bearish = config.PCR_BEARISH
        self.pcr_bullish = config.PCR_BULLISH
        self.trades_today= 0
        self.last_trade_t= None
        self.strategy_engine = StrategyEngine()
        self.eod_done    = False
        self.cycle       = 0
        self.baseline_oi = {}  # session-start OI per strike for intraday ΔOI chart


# ════════════════════════════════════════════════════════════════
#  Core processing pipeline  (one call per refresh cycle)
# ════════════════════════════════════════════════════════════════
def process_cycle(data: dict, symbol: str, vix: str,
                  demo: bool, cycle: int,
                  selected_expiry: str = None):

    # Validate response structure before touching any key
    if not isinstance(data, dict) or "records" not in data:
        print(f"process_cycle: bad data shape — keys={list(data.keys()) if isinstance(data,dict) else type(data)}")
        return None, {}, pd.DataFrame(), {}, {}, {}, {}

    rec = data["records"]
    if not isinstance(rec, dict):
        print(f"process_cycle: 'records' is not a dict (type={type(rec)})")
        return None, {}, pd.DataFrame(), {}, {}, {}, {}

    spot = rec.get("underlyingValue")
    if not spot:
        print(f"process_cycle: missing underlyingValue — rec keys={list(rec.keys())}")
        return None, {}, pd.DataFrame(), {}, {}, {}, {}
    spot = float(spot)
    avail  = rec.get("expiryDates", [])
    expiry = (selected_expiry if selected_expiry and selected_expiry in avail
              else avail[0] if avail else "")

    raw_data = rec.get("data", [])
    if not raw_data:
        print("process_cycle: empty data list in records")
        return None, {}, pd.DataFrame(), {}, {}, {}, {}

    df = build_df(raw_data, expiry)
    if df.empty:
        return None, {}, pd.DataFrame(), {}, {}, {}, {}

    # ── Intraday OI delta from session-start baseline ─────────────
    # Shoonya's daychngoi (→ CE_Chg) is 0 for most option strikes.
    # We track a session-start baseline and compute the real intraday delta.
    _strike_list = df["Strike"].astype(int).tolist()
    if not state.baseline_oi:
        # Cycle 1: snapshot current OI as the zero reference.
        for _s, _ceo, _peo in zip(_strike_list, df["CE_OI"], df["PE_OI"]):
            state.baseline_oi[_s] = {"CE": _ceo, "PE": _peo}
        df["CE_Intraday_Chg"] = 0.0
        df["PE_Intraday_Chg"] = 0.0
    else:
        df["CE_Intraday_Chg"] = [
            row["CE_OI"] - state.baseline_oi.get(int(row["Strike"]), {"CE": row["CE_OI"]})["CE"]
            for _, row in df.iterrows()
        ]
        df["PE_Intraday_Chg"] = [
            row["PE_OI"] - state.baseline_oi.get(int(row["Strike"]), {"PE": row["PE_OI"]})["PE"]
            for _, row in df.iterrows()
        ]

    total_ce = df["CE_OI"].sum()
    if total_ce == 0:
        return None, {}, pd.DataFrame(), {}, {}, {}, {}
    total_pe = df["PE_OI"].sum()

    # ── PCR ────────────────────────────────────────────────────
    pcr           = round(total_pe / total_ce, 3)
    local_pcr     = calc_localized_pcr(df, spot)
    pcr_for_bias  = local_pcr if local_pcr is not None else pcr
    pcr_signal, pcr_signal_color = generate_pcr_signal(pcr_for_bias)

    # ── Multi-expiry PCR (Weekly + Monthly) ──────────────────
    expiry_map  = classify_expiries(avail)
    weekly_exp  = expiry_map.get("weekly")
    monthly_exp = expiry_map.get("monthly")
    weekly_pcr  = calc_pcr_for_expiry(raw_data, weekly_exp)  if weekly_exp  else None
    monthly_pcr = calc_pcr_for_expiry(raw_data, monthly_exp) if monthly_exp else None
    total_oi    = float(df["CE_OI"].sum() + df["PE_OI"].sum())
    pcr_data    = pcr_series.update(pcr_for_bias, weekly_pcr, monthly_pcr, total_oi)

    # ── Day quality + trade calendar ──────────────────────────
    day_qual = get_day_quality(symbol)

    # ── Resistance / Support ───────────────────────────────────
    # Resistance = nearest overhead strike (above spot) with max CE OI
    # Support    = nearest underfoot strike (below spot) with max PE OI
    # Constrained to ATM ± 20 strikes to avoid irrelevant far-OTM picks.
    _step  = strike_step(symbol)
    _range = _step * 20   # 20 strikes each side (1000 NF / 2000 BNF)

    # ── Intraday resistance / support (for signal generation) ────
    # Constrained: overhead CE wall and underfoot PE wall.
    # Used for trade entry context (direction of nearest OI pressure).
    _ce_above = df[(df["Strike"] > spot) & (df["Strike"] <= spot + _range)].copy()
    _pe_below = df[(df["Strike"] < spot) & (df["Strike"] >= spot - _range)].copy()

    if not _ce_above.empty:
        _ce_above = _ce_above[_ce_above["CE_OI"] > 0]
        resistance = int(_ce_above.loc[_ce_above["CE_OI"].idxmax(), "Strike"])                      if not _ce_above.empty else int(spot + _step * 5)
    else:
        resistance = int(spot + _step * 5)

    if not _pe_below.empty:
        _pe_below = _pe_below[_pe_below["PE_OI"] > 0]
        support = int(_pe_below.loc[_pe_below["PE_OI"].idxmax(), "Strike"])                   if not _pe_below.empty else int(spot - _step * 5)
    else:
        support = int(spot - _step * 5)

    # ── Forward-looking levels (for EOD "Levels to Watch Tomorrow") ──
    # Uses UNCONSTRAINED max OI strikes — these represent where writers have
    # the most exposure regardless of whether they are currently above/below spot.
    _full = df[df["CE_OI"] > 0].copy()
    _full_pe = df[df["PE_OI"] > 0].copy()
    ce_max_oi_strike = int(_full.loc[_full["CE_OI"].idxmax(), "Strike"])                        if not _full.empty else resistance
    pe_max_oi_strike = int(_full_pe.loc[_full_pe["PE_OI"].idxmax(), "Strike"])                        if not _full_pe.empty else support

    # ── Volume-weighted OI score ───────────────────────────────
    weighted_net_score = int(
        (df["CE_Chg"] * df["CE_Vol"]).sum()
        - (df["PE_Chg"] * df["PE_Vol"]).sum()
    )
    raw_net_score = int(df["CE_Chg"].sum() - df["PE_Chg"].sum())
    max_pain = calc_max_pain(df)

    # ── RoC alerts ─────────────────────────────────────────────
    roc_alerts = compute_roc_alerts(raw_data, expiry)

    # ── IV analytics (v5.3 NEW) ────────────────────────────────
    atm_iv      = calc_atm_iv(df, spot)
    daily_iv    = iv_tracker.record(atm_iv)
    iv_skew     = calc_iv_skew(df, spot)
    hist_summ   = iv_history.summary(atm_iv) if atm_iv else {}

    iv_data = {
        "atm_iv":       atm_iv,
        "skew":         iv_skew,
        "daily":        daily_iv,
        "hist_summary": hist_summ,
    }

    # ── 4-factor bias votes ────────────────────────────────────
    pcr_bias   = ("BEARISH" if pcr_for_bias > state.pcr_bearish else
                  "BULLISH" if pcr_for_bias < state.pcr_bullish else "NEUTRAL")
    score_bias = ("BEARISH" if weighted_net_score > 0 else
                  "BULLISH" if weighted_net_score < 0 else "NEUTRAL")
    pain_bias  = ("BULLISH" if spot < max_pain else
                  "BEARISH" if spot > max_pain else "NEUTRAL")
    rsi_val, vwap_val, tech_signal = state.strategy_engine.on_tick(spot)

    from signals.advanced_analytics import run_advanced_analytics
    adv = run_advanced_analytics(df, spot, symbol=symbol)  # BUG-06: pass symbol for step-aware momentum
    dealer_vote = adv["dealer"]["vote"]

    votes = [pcr_bias, score_bias, pain_bias, tech_signal, dealer_vote]
    bias  = max(set(votes), key=votes.count)

    # Track trend persistence for scoring
    if bias == state._last_bias and bias != "NEUTRAL":
        state.consecutive_bias_cycles += 1
    else:
        state.consecutive_bias_cycles = 1 if bias != "NEUTRAL" else 0
    state._last_bias = bias

    # ── Recommendations ────────────────────────────────────────
    # (strategy classifier runs AFTER score is known — removed placeholder call BUG-10)
    recs = recommend_strikes(df, spot, bias, max_pain, resistance, support, symbol)
    if len(recs) < 3:
        return None, {}, pd.DataFrame(), {}, {}, {}, {}

    # ── Signal score ───────────────────────────────────────────
    # pcr_trend_bars: +N if PCR has been rising N cycles (bearish confirm),
    #                  -N if falling N cycles (bullish confirm)
    _pcr_hist = list(pcr_series.local) if pcr_series.local else []
    if len(_pcr_hist) >= 3:
        _window = _pcr_hist[-min(5, len(_pcr_hist)):]
        _rising = sum(1 for i in range(1, len(_window)) if _window[i] > _window[i-1])
        _falling= sum(1 for i in range(1, len(_window)) if _window[i] < _window[i-1])
        pcr_trend_bars = _rising if _rising > _falling else -_falling
    else:
        pcr_trend_bars = 0

    score, breakdown, unanimous = score_signal(
        bias, votes, pcr_for_bias, weighted_net_score, spot, max_pain,
        vix, roc_alerts, tech_signal, dealer_vote=dealer_vote,
        pcr_trend_bars=pcr_trend_bars
    )
    # Re-run classifier with real score now known
    strat_class = classify_strategy(
        bias         = bias,
        ivr          = hist_summ.get("ivr"),
        ivp          = hist_summ.get("ivp"),
        atm_iv       = atm_iv,
        score        = score,
        days_to_exp  = day_qual["days_to_weekly"],
        day_quality  = day_qual["composite_score"],
        pcr_div_sig  = pcr_data.get("div_signal", "ALIGNED"),
    )

    # ── Trade filter ───────────────────────────────────────────
    take, filter_reason = should_take_trade(score, bias, spot=spot)
    if take:
        register_trade_taken()

    # ── CSV log ────────────────────────────────────────────────
    log_row = pd.DataFrame([{
        "Timestamp":    rec.get("timestamp", now_ist().strftime("%d-%b-%Y %H:%M:%S")), "Spot": spot, "VIX": vix,
        "PCR_Full": pcr, "PCR_Local": local_pcr, "PCR_Signal": pcr_signal,
        "MaxPain": max_pain, "Resistance": resistance, "Support": support,
        "CE_MaxOI_Strike": ce_max_oi_strike, "PE_MaxOI_Strike": pe_max_oi_strike,
        "WeightedScore": weighted_net_score, "RawOIScore": raw_net_score,
        "Bias": bias,
        "RSI":    round(rsi_val,  2) if rsi_val  else "warming",
        "VWAP":   round(vwap_val, 2) if vwap_val else "warming",
        "TechSignal": tech_signal,
        "Score": score, "Unanimous": unanimous,
        "Taken": take, "SkipReason": filter_reason,
        "TradesToday": state.daily_trades_taken,
        "Rec1": f"{recs[0].strike}{recs[0].opt_type}@{recs[0].premium}",
        "Rec2": f"{recs[1].strike}{recs[1].opt_type}@{recs[1].premium}",
        "Rec3": f"{recs[2].strike}{recs[2].opt_type}@{recs[2].premium}",
        "PCR_Bands": f"{state.pcr_bullish}/{state.pcr_bearish}",
        # v5.3 IV columns
        "ATM_IV": atm_iv, "IVR": hist_summ.get("ivr"), "IVP": hist_summ.get("ivp"),
        "IV_Skew_Pct": iv_skew.get("skew_pct"), "IV_Skew_Dir": iv_skew.get("direction"),
        # v5.7 new fields
        "PCR_Weekly": weekly_pcr, "PCR_Monthly": monthly_pcr,
        "PCR_EMA20": pcr_data.get("ema20"), "PCR_VWAP": pcr_data.get("vwap"),
        "PCR_Divergence": pcr_data.get("divergence"), "PCR_DivSignal": pcr_data.get("div_signal"),
        "DayScore": day_qual["composite_score"], "DayLabel": day_qual["day_profile"]["label"],
        "TimeWindow": day_qual["time_window"]["label"],
        "DTE_Weekly": day_qual["days_to_weekly"], "DTE_Monthly": day_qual["days_to_monthly"],
        "Strategy": strat_class["strategy_name"], "StrategyType": strat_class["strategy_type"],
        "Conviction": strat_class["conviction"],
    }])
    _csv_path = config.LOG_FILE  # Always use current config, not module-level import
    log_row.to_csv(_csv_path, mode="a",
                   header=not os.path.exists(_csv_path), index=False)

    # ── Render (terminal / Colab) ──────────────────────────────
    render(df, symbol, spot, vix, expiry, pcr_for_bias, bias, max_pain,
           resistance, support, weighted_net_score, roc_alerts, recs, demo, cycle,
           score=score, score_breakdown=breakdown, taken=take,
           skip_reason=filter_reason, unanimous=unanimous,
           rsi=rsi_val, vwap=vwap_val, tech_signal=tech_signal,
           pcr_signal=pcr_signal, pcr_signal_color=pcr_signal_color,
           local_pcr=local_pcr, iv_data=iv_data, adv=adv)

    sig = Signal(
        time=now_ist().strftime("%H:%M"),
        bias=bias, spot=spot, pcr=pcr_for_bias,
        rec1=recs[0], rec2=recs[1], rec3=recs[2],
        score=score, taken=take, skip_reason=filter_reason,
        votes_unanimous=unanimous,
        rsi=rsi_val, vwap=vwap_val, tech_signal=tech_signal,
        atm_iv=atm_iv, ivr=hist_summ.get("ivr"), ivp=hist_summ.get("ivp"),
        iv_skew_pct=iv_skew.get("skew_pct"), iv_skew_dir=iv_skew.get("direction", "N/A"),
        symbol=symbol,
    )
    # BUG-01 fix: pack all locals that call sites need into a ctx dict.
    # Previously these were referenced by name at call sites → NameError.
    _ctx = {
        "max_pain":         max_pain,
        "resistance":       resistance,
        "support":          support,
        "weighted_net_score": weighted_net_score,
        "local_pcr":        local_pcr,
        "pcr_signal":       pcr_signal,
        "pcr_signal_color": pcr_signal_color,
        "roc_alerts":       roc_alerts,       # BUG-07 fix: expose for Telegram
    }
    return sig, adv, df, pcr_data, day_qual, strat_class, _ctx


# ════════════════════════════════════════════════════════════════
#  State writer for Streamlit frontend
# ════════════════════════════════════════════════════════════════
def _build_iv_chart(df, spot: float) -> dict:
    """
    Build IV skew chart data filtered to ATM ±12 strikes.
    Strips zero-IV rows so the chart doesn't flatline outside market hours.
    Returns {} if no valid IV data exists.
    """
    if df.empty or "CE_IV" not in df.columns:
        return {}

    from config import SYMBOL
    step = 100 if SYMBOL == "BANKNIFTY" else 50
    atm  = round(spot / step) * step

    # Keep only ±12 strikes around ATM
    mask = (df["Strike"] >= atm - 12 * step) & (df["Strike"] <= atm + 12 * step)
    sub  = df[mask].copy()

    if sub.empty:
        return {}

    # Only include rows where at least one IV is non-zero
    has_iv = (sub["CE_IV"].fillna(0) > 0) | (sub["PE_IV"].fillna(0) > 0)
    sub = sub[has_iv]

    if sub.empty:
        # All zeros — return flag so Streamlit can show a message
        return {"no_data": True}

    return {
        "strikes": sub["Strike"].astype(int).tolist(),
        "ce_iv":   sub["CE_IV"].fillna(0).tolist(),
        "pe_iv":   sub["PE_IV"].fillna(0).tolist(),
        "atm":     int(atm),
    }


def _write_streamlit_state(sig, adv: dict, df, spot: float, vix: str,
                           pcr_data: dict = None, day_qual: dict = None,
                           strat_class: dict = None,
                           max_pain: float = 0, resistance: int = 0,
                           support: int = 0, net_score: int = 0,
                           local_pcr: float = None, pcr_signal: str = "NEUTRAL",
                           pcr_signal_color: str = "#f39c12"):
    """Write current cycle state to dashboard_state.json for Streamlit."""
    from core.nse_fetcher import nearest_strike as _nearest_strike
    atm_df  = df[(df["Strike"] >= spot * 0.96) & (df["Strike"] <= spot * 1.04)].copy()
    atm_str = _nearest_strike(spot, config.SYMBOL)

    # Weighted direction for the OI Profile header text
    if net_score > 0:
        wtd_dir, wtd_clr = "Bearish (CE buildup)", "#e74c3c"
    elif net_score < 0:
        wtd_dir, wtd_clr = "Bullish (PE buildup)", "#2ecc71"
    else:
        wtd_dir, wtd_clr = "Neutral", "#f39c12"

    # Build oi_chart with all fields needed to replicate the 3-panel chart
    oi_chart_data: dict = {}
    if not atm_df.empty:
        ce_vol = atm_df["CE_Vol"].tolist() if "CE_Vol" in atm_df.columns else [0]*len(atm_df)
        oi_chart_data = {
            "strikes":    atm_df["Strike"].astype(int).tolist(),
            "ce_oi":      atm_df["CE_OI"].tolist(),
            "pe_oi":      atm_df["PE_OI"].tolist(),
            "ce_chg":     (atm_df["CE_Intraday_Chg"].tolist()
                           if "CE_Intraday_Chg" in atm_df.columns
                           else atm_df["CE_Chg"].tolist()),
            "pe_chg":     (atm_df["PE_Intraday_Chg"].tolist()
                           if "PE_Intraday_Chg" in atm_df.columns
                           else atm_df["PE_Chg"].tolist()),
            "ce_vol":     ce_vol,
            "atm":        int(atm_str),
            "max_pain":   int(max_pain)   if max_pain   else 0,
            "resistance": int(resistance) if resistance else 0,
            "support":    int(support)    if support    else 0,
            "net_score":  net_score,
            "wtd_dir":    wtd_dir,
            "wtd_clr":    wtd_clr,
            "local_pcr":  round(local_pcr, 3) if local_pcr else None,
            "pcr_signal": pcr_signal,
            "pcr_signal_color": pcr_signal_color,
        }

    # Build iv_chart with skew annotation data
    iv_chart_data = _build_iv_chart(df, spot)
    if sig and sig.iv_skew_pct is not None:
        iv_chart_data["skew_pct"]      = sig.iv_skew_pct
        iv_chart_data["skew_dir"]      = sig.iv_skew_dir
        iv_chart_data["atm_iv_val"]    = sig.atm_iv

    state_data = {
        "spot":    spot,
        "vix":     vix,
        "pcr":     sig.pcr    if sig else 0,
        "bias":    sig.bias   if sig else "N/A",
        "score":   sig.score  if sig else 0,
        "atm_iv":  sig.atm_iv if sig else "N/A",
        "ivr":     sig.ivr    if sig else "N/A",
        "ivp":     sig.ivp    if sig else "N/A",
        "adv":     adv or {},
        "recs":    ([{"label": r.label, "strike": r.strike, "opt_type": r.opt_type,
                      "premium": r.premium, "sl": r.sl, "target": r.target,
                      "reason": r.reason}
                     for r in [sig.rec1, sig.rec2, sig.rec3]] if sig else []),
        "oi_chart": oi_chart_data,
        "iv_chart": iv_chart_data,
        "ts":     now_ist().strftime("%H:%M:%S"),
        "symbol": config.SYMBOL,
        "pcr_series":  pcr_data    or {},
        "day_quality": day_qual    or {},
        "strat_class": strat_class or {},
    }
    state_file = f"dashboard_state_{config.SYMBOL}.json"
    try:
        with open(state_file, "w") as f:
            json.dump(state_data, f)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
#  Dual-symbol mode  (--both flag)
#  Runs NIFTY + BANKNIFTY in ONE process with ONE shared Shoonya
#  session — avoids the session-kick problem of running two processes.
# ════════════════════════════════════════════════════════════════
def _run_dual_mode(symbols=None):
    """
    Single-process dual-symbol loop.
    Fetches NIFTY then BANKNIFTY (or any symbols list) sequentially
    every REFRESH_RATE seconds, writing separate state files for each.

    Usage:
        python main.py --both
        python main.py --both --symbols NIFTY BANKNIFTY FINNIFTY
    """
    if symbols is None:
        # Parse --symbols list if provided, else default NIFTY + BANKNIFTY
        if "--symbols" in sys.argv:
            idx = sys.argv.index("--symbols")
            symbols = [s.upper() for s in sys.argv[idx+1:]
                       if s.upper() in ("NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY")]
        if not symbols:
            symbols = ["NIFTY", "BANKNIFTY"]

    market_open = is_market_open()
    use_demo    = (True  if config.DEMO_MODE is True  else
                   False if config.DEMO_MODE is False else
                   not market_open)

    print("=" * 68)
    print(f"  NSE LIVE OI DASHBOARD  v5.7  [DUAL MODE: {' + '.join(symbols)}]")
    print(f"  One session, one process — no session-kick conflict")
    print(f"  Mode: {'DEMO (markets closed)' if use_demo else 'LIVE'}")
    print(f"  Refresh: {REFRESH_RATE}s per full cycle")
    print("=" * 68)

    engines = [SymbolEngine(sym) for sym in symbols]

    if not use_demo:
        print("Logging in to Shoonya (shared session) …")
        create_session()
        print("✓ Logged in")
    init_telegram()

    while True:
        try:
            cycle_start = time.time()

            # Single VIX fetch shared by all symbols
            if use_demo:
                vix = str(round(14.5 + math.sin(sum(e.cycle for e in engines) * 0.3) * 2.5, 2))
            else:
                vix = fetch_vix()

            for eng in engines:
                try:
                    print(f"  [{eng.symbol}] cycle {eng.cycle+1} …", end=" ", flush=True)
                    run_symbol_cycle(eng, vix, use_demo)
                    print("✓")
                except Exception as e:
                    print(f"✗ {e}")

            elapsed = time.time() - cycle_start
            sleep_t = max(1, REFRESH_RATE - elapsed)
            time.sleep(sleep_t if not use_demo else 5)

        except KeyboardInterrupt:
            print("\n\nDual-mode stopped by user.")
            for eng in engines:
                if eng.signal_log:
                    last = eng.signal_log[-1].spot
                    for s in eng.signal_log:
                        if s.spot_exit is None: s.spot_exit = last
            break


# ════════════════════════════════════════════════════════════════
#  Single-symbol cycle runner (used by both single and dual mode)
# ════════════════════════════════════════════════════════════════
def run_symbol_cycle(eng: "SymbolEngine", vix: str, use_demo: bool) -> None:
    """
    Run one fetch-process-write cycle for a single symbol.
    Uses the SymbolEngine's private singletons — fully isolated from
    other symbols running in the same process.
    """
    # Temporarily point module-level globals to this engine's state
    # (needed because process_cycle reads config.SYMBOL etc.)
    # Declare globals first to avoid SyntaxError
    global SYMBOL, LOT_SIZE, LOG_FILE

    _prev_sym = config.SYMBOL
    _prev_lot = config.LOT_SIZE
    _prev_log = config.LOG_FILE
    _prev_iv  = config.IV_HIST_FILE

    config.SYMBOL       = eng.symbol
    config.LOT_SIZE     = eng.lot_size
    config.LOG_FILE     = eng.log_file
    config.IV_HIST_FILE = eng.iv_hist_file
    # Also sync module-level globals (used by process_cycle CSV write)
    SYMBOL   = eng.symbol
    LOT_SIZE = eng.lot_size
    LOG_FILE = eng.log_file

    # Swap module-level singletons
    global iv_tracker, iv_history, pcr_series
    _save_tracker = iv_tracker
    _save_history = iv_history
    _save_pcr     = pcr_series
    iv_tracker = eng.iv_tracker
    iv_history = eng.iv_history
    pcr_series = eng.pcr_series

    # Swap state singletons
    import state as _state
    _save_log    = _state.signal_log
    _save_trades = _state.daily_trades_taken
    _save_last_t = _state.last_trade_time
    _save_bull   = _state.pcr_bullish
    _save_bear   = _state.pcr_bearish
    _save_engine   = _state.strategy_engine
    _save_baseline = _state.baseline_oi
    _state.signal_log          = eng.signal_log
    _state.daily_trades_taken  = eng.trades_today
    _state.last_trade_time     = eng.last_trade_t
    _state.pcr_bullish         = eng.pcr_bullish
    _state.pcr_bearish         = eng.pcr_bearish
    _state.strategy_engine     = eng.strategy_engine
    _state.baseline_oi         = eng.baseline_oi

    try:
        eng.cycle += 1
        if use_demo:
            data = demo_data(eng.symbol, eng.cycle)
        else:
            from core.nse_fetcher import fetch_chain, fetch_vix
            data = fetch_chain(None, eng.symbol)
            if not data:
                print(f"  [{eng.symbol}] fetch_chain returned no data")
                return

        result = process_cycle(data, eng.symbol, vix, use_demo, eng.cycle)
        sig, adv, cycle_df, pcr_data, day_qual, strat_class, _ctx = result  # BUG-01
        if sig is None:
            return

        _write_streamlit_state(
            sig, adv, cycle_df,
            data["records"]["underlyingValue"], vix,
            pcr_data=pcr_data, day_qual=day_qual, strat_class=strat_class,
            max_pain=_ctx["max_pain"], resistance=_ctx["resistance"],
            support=_ctx["support"], net_score=_ctx["weighted_net_score"],
            local_pcr=_ctx["local_pcr"], pcr_signal=_ctx["pcr_signal"],
            pcr_signal_color=_ctx["pcr_signal_color"])

        # Breakout / Telegram alerts
        brk = adv.get("breakout", {}) if adv else {}
        if brk.get("breakout"):
            send_breakout(eng.symbol, data["records"]["underlyingValue"],
                          brk["signal"], brk["resistance"], brk["support"])

        if sig:
            eng.signal_log.append(sig)
            if len(eng.signal_log) >= 2:
                prev = eng.signal_log[-2]
                prev.spot_exit = sig.spot
                prev.outcome = ("WIN" if
                    (prev.bias == "BULLISH" and sig.spot > prev.spot)
                    or (prev.bias == "BEARISH" and sig.spot < prev.spot)
                    else "LOSS")
                auto_tune(prev)

        if sig and (sig.taken or sig.score >= 70):
            send_signal(sig, adv)

        if is_eod() and not eng.eod_done and not use_demo:
            eng.eod_done = True
            final_spot = data["records"]["underlyingValue"]
            for s in eng.signal_log:
                if s.spot_exit is None: s.spot_exit = final_spot
            run_eod_backtest(final_spot)
            current_iv = eng.iv_tracker._summary().get("current")
            if current_iv:
                eng.iv_history.update(current_iv)
            wins   = sum(1 for s in eng.signal_log if s.outcome == "WIN")
            losses = sum(1 for s in eng.signal_log if s.outcome == "LOSS")
            send_eod_summary(eng.symbol, wins, losses, len(eng.signal_log), final_spot)

        # Sync state back to engine
        eng.trades_today  = _state.daily_trades_taken
        eng.last_trade_t  = _state.last_trade_time
        eng.pcr_bullish   = _state.pcr_bullish
        eng.pcr_bearish   = _state.pcr_bearish
        eng.baseline_oi   = _state.baseline_oi

    finally:
        # Always restore config and module-level globals
        config.SYMBOL       = _prev_sym
        config.LOT_SIZE     = _prev_lot
        config.LOG_FILE     = _prev_log
        config.IV_HIST_FILE = _prev_iv
        SYMBOL   = _prev_sym
        LOT_SIZE = _prev_lot
        LOG_FILE = _prev_log
        iv_tracker = _save_tracker
        iv_history = _save_history
        pcr_series = _save_pcr
        _state.signal_log         = _save_log
        _state.daily_trades_taken = _save_trades
        _state.last_trade_time    = _save_last_t
        _state.pcr_bullish        = _save_bull
        _state.pcr_bearish        = _save_bear
        _state.strategy_engine    = _save_engine
        _state.baseline_oi        = _save_baseline


# ════════════════════════════════════════════════════════════════
#  Main loop
# ════════════════════════════════════════════════════════════════
def main():
    # ── Symbol override: python main.py --symbol BANKNIFTY ───────
    if "--symbol" in sys.argv:
        idx = sys.argv.index("--symbol")
        if idx + 1 < len(sys.argv):
            sym = sys.argv[idx + 1].upper()
            if sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                config.SYMBOL   = sym
                config.LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 30,
                                   "FINNIFTY": 40, "MIDCPNIFTY": 75}.get(sym, 65)
                import datetime as _dt
                _today = _dt.datetime.now().strftime("%Y%m%d")
                config.LOG_FILE     = f"{sym}_OI_{_today}.csv"
                config.PLOT_FILE    = f"{sym}_chart.png"
                config.IV_HIST_FILE = f"{sym}_iv_history.csv"
                # Re-import globals that were bound at module load time
                global SYMBOL, LOT_SIZE, LOG_FILE
                SYMBOL   = config.SYMBOL
                LOT_SIZE = config.LOT_SIZE
                LOG_FILE = config.LOG_FILE

    if "--gui" in sys.argv:
        config.DISPLAY_MODE = "tkinter"
    if "--terminal" in sys.argv:
        config.DISPLAY_MODE = "terminal"

    # ── Dual-symbol mode: python main.py --both ────────────────
    if "--both" in sys.argv:
        _run_dual_mode()
        return

    # ── Tkinter mode ──────────────────────────────────────────
    if config.DISPLAY_MODE == "tkinter":
        try:
            import tkinter as tk
            from display.gui import OITkApp
            root = tk.Tk()
            app  = OITkApp(root, process_cycle, iv_tracker, iv_history)
            try:
                root.mainloop()
            except KeyboardInterrupt:
                pass
            finally:
                if state.signal_log:
                    last = state.signal_log[-1].spot
                    for s in state.signal_log:
                        if s.spot_exit is None: s.spot_exit = last
                    run_eod_backtest(last)
                    iv_history.update(iv_tracker._summary().get("current") or 0)
                print(f"Log saved → {LOG_FILE}")
            return
        except ImportError:
            print("Tkinter unavailable — falling back to terminal mode.")
            config.DISPLAY_MODE = "terminal"

    # ── Terminal / Colab mode ─────────────────────────────────
    market_open = is_market_open()
    use_demo    = (True  if config.DEMO_MODE is True  else
                   False if config.DEMO_MODE is False else
                   not market_open)

    print("=" * 68)
    print(f"  NSE LIVE OI DASHBOARD  v5.7  [{SYMBOL}]")
    print(f"  Env: {'Colab' if IN_COLAB else 'Local'}  |  Lot: {LOT_SIZE}  |  Refresh: {REFRESH_RATE}s")
    print(f"  Mode: {'DEMO (markets closed)' if use_demo else 'LIVE'}")
    print(f"  4-vote: PCR + OI Score + Max Pain + RSI/VWAP")
    print(f"  v5.3 NEW: ATM IV · IVR · IVP · Daily IV · IV Skew (which side is heavy)")
    print(f"  IV history: {iv_history.filepath}  ({len(iv_history.history)} days loaded)")
    if use_demo:
        print(f"  Next market open: {next_open_str()}")
    print("=" * 68)

    if not use_demo:
        create_session()
    init_telegram()
    cycle    = 0
    eod_done = False

    while True:
        try:
            cycle += 1
            if use_demo:
                vix  = str(round(14.5 + math.sin(cycle * 0.5) * 2.5
                                 + random.uniform(-0.3, 0.3), 2))
                data = demo_data(SYMBOL, cycle)
            else:
                vix  = fetch_vix()
                data = fetch_chain(None, SYMBOL)
                if not data:
                    print(f"No data from NSE. "
                          f"Next open: {next_open_str()}. Retrying in {REFRESH_RATE}s...")
                    if not is_market_open():
                        print("Switching to Demo Mode...")
                        use_demo = True
                    time.sleep(REFRESH_RATE)
                    continue

            result = process_cycle(data, SYMBOL, vix, use_demo, cycle)
            sig, adv, cycle_df, pcr_data, day_qual, strat_class, _ctx = result  # BUG-01
            if sig is None:
                time.sleep(REFRESH_RATE); continue

            # Write state for Streamlit
            _write_streamlit_state(sig, adv, cycle_df,
                                   data["records"]["underlyingValue"], vix,
                                   pcr_data=pcr_data, day_qual=day_qual,
                                   strat_class=strat_class,
                                   max_pain=_ctx["max_pain"],
                                   resistance=_ctx["resistance"],
                                   support=_ctx["support"],
                                   net_score=_ctx["weighted_net_score"],
                                   local_pcr=_ctx["local_pcr"],
                                   pcr_signal=_ctx["pcr_signal"],
                                   pcr_signal_color=_ctx["pcr_signal_color"])

            # Breakout alert via Telegram
            brk = adv.get("breakout", {}) if adv else {}
            if brk.get("breakout"):
                send_breakout(SYMBOL, data["records"]["underlyingValue"],
                              brk["signal"], brk["resistance"], brk["support"])

            # RoC alert via Telegram — BUG-07 fix: use ctx not dir()
            _roc = _ctx.get("roc_alerts", [])
            if _roc:
                send_roc_alert(SYMBOL, _roc)

            if sig:
                state.signal_log.append(sig)
                if len(state.signal_log) >= 2:
                    prev = state.signal_log[-2]
                    prev.spot_exit = sig.spot
                    prev.outcome   = ("WIN" if
                        (prev.bias == "BULLISH" and sig.spot > prev.spot)
                        or (prev.bias == "BEARISH" and sig.spot < prev.spot)
                        else "LOSS")
                    auto_tune(prev)

            # Send Telegram for taken trades or score > 70
            if sig and (sig.taken or sig.score >= 70):
                send_signal(sig, adv)

            if is_eod() and not eod_done and not use_demo:
                eod_done = True
                final_spot = data["records"]["underlyingValue"]
                for s in state.signal_log:
                    if s.spot_exit is None: s.spot_exit = final_spot
                run_eod_backtest(final_spot)
                # Save today's closing IV to history
                current_iv = iv_tracker._summary().get("current")
                if current_iv:
                    iv_history.update(current_iv)
                wins   = sum(1 for s in state.signal_log if s.outcome == "WIN")
                losses = sum(1 for s in state.signal_log if s.outcome == "LOSS")
                send_eod_summary(SYMBOL, wins, losses, len(state.signal_log), final_spot)

            time.sleep(5 if use_demo else REFRESH_RATE)

        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            if state.signal_log:
                last = state.signal_log[-1].spot
                for s in state.signal_log:
                    if s.spot_exit is None: s.spot_exit = last
                run_eod_backtest(last)
            print(f"Log saved → {LOG_FILE}")
            break
        except Exception as e:
            print(f"Error: {e}  retrying in 15s...")
            time.sleep(15)


if __name__ == "__main__":
    main()
