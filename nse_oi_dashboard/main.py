#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════
#  NSE Live OI Dashboard  v5.3
#  Entry point — terminal mode (default) or Tkinter GUI (--gui)
#
#  Usage:
#    python main.py             # terminal / Colab
#    python main.py --gui       # Tkinter GUI (local PC only)
#
#  pip install requests pandas matplotlib yfinance
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
    build_df, demo_data, nearest_strike
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
from display.terminal import render
from backtest.eod_backtest import Signal, run_eod_backtest

# ── Singleton IV objects (persist across cycles) ─────────────────
iv_tracker = IVTracker()
iv_history = IVHistory()


# ════════════════════════════════════════════════════════════════
#  Core processing pipeline  (one call per refresh cycle)
# ════════════════════════════════════════════════════════════════
def process_cycle(data: dict, symbol: str, vix: str,
                  demo: bool, cycle: int,
                  selected_expiry: str = None) -> Signal | None:

    # Validate response structure before touching any key
    if not isinstance(data, dict) or "records" not in data:
        print(f"process_cycle: bad data shape — keys={list(data.keys()) if isinstance(data,dict) else type(data)}")
        return None

    rec = data["records"]
    if not isinstance(rec, dict):
        print(f"process_cycle: 'records' is not a dict (type={type(rec)})")
        return None

    spot = rec.get("underlyingValue")
    if not spot:
        print(f"process_cycle: missing underlyingValue — rec keys={list(rec.keys())}")
        return None
    spot = float(spot)
    avail  = rec.get("expiryDates", [])
    expiry = (selected_expiry if selected_expiry and selected_expiry in avail
              else avail[0] if avail else "")

    raw_data = rec.get("data", [])
    if not raw_data:
        print("process_cycle: empty data list in records")
        return None

    df = build_df(raw_data, expiry)
    if df.empty:
        return None

    total_ce = df["CE_OI"].sum()
    if total_ce == 0:
        return None
    total_pe = df["PE_OI"].sum()

    # ── PCR ────────────────────────────────────────────────────
    pcr           = round(total_pe / total_ce, 3)
    local_pcr     = calc_localized_pcr(df, spot)
    pcr_for_bias  = local_pcr if local_pcr is not None else pcr
    pcr_signal, pcr_signal_color = generate_pcr_signal(pcr_for_bias)

    # ── Resistance / Support (from max ΔOI — fresh money) ─────
    resistance = int(df.loc[df["CE_Chg"].idxmax(), "Strike"])
    support    = int(df.loc[df["PE_Chg"].idxmax(), "Strike"])

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

    votes = [pcr_bias, score_bias, pain_bias, tech_signal]
    bias  = max(set(votes), key=votes.count)

    # ── Recommendations ────────────────────────────────────────
    recs = recommend_strikes(df, spot, bias, max_pain, resistance, support, symbol)
    if len(recs) < 3:
        return None

    # ── Signal score ───────────────────────────────────────────
    score, breakdown, unanimous = score_signal(
        bias, votes, pcr_for_bias, weighted_net_score, spot, max_pain,
        vix, roc_alerts, tech_signal
    )

    # ── Trade filter ───────────────────────────────────────────
    take, filter_reason = should_take_trade(score, bias)
    if take:
        register_trade_taken()

    # ── CSV log ────────────────────────────────────────────────
    log_row = pd.DataFrame([{
        "Timestamp":    rec.get("timestamp", now_ist().strftime("%d-%b-%Y %H:%M:%S")), "Spot": spot, "VIX": vix,
        "PCR_Full": pcr, "PCR_Local": local_pcr, "PCR_Signal": pcr_signal,
        "MaxPain": max_pain, "Resistance": resistance, "Support": support,
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
    }])
    log_row.to_csv(LOG_FILE, mode="a",
                   header=not os.path.exists(LOG_FILE), index=False)

    # ── Render (terminal / Colab) ──────────────────────────────
    render(df, symbol, spot, vix, expiry, pcr_for_bias, bias, max_pain,
           resistance, support, weighted_net_score, roc_alerts, recs, demo, cycle,
           score=score, score_breakdown=breakdown, taken=take,
           skip_reason=filter_reason, unanimous=unanimous,
           rsi=rsi_val, vwap=vwap_val, tech_signal=tech_signal,
           pcr_signal=pcr_signal, pcr_signal_color=pcr_signal_color,
           local_pcr=local_pcr, iv_data=iv_data)

    return Signal(
        time=now_ist().strftime("%H:%M"),
        bias=bias, spot=spot, pcr=pcr_for_bias,
        rec1=recs[0], rec2=recs[1], rec3=recs[2],
        score=score, taken=take, skip_reason=filter_reason,
        votes_unanimous=unanimous,
        rsi=rsi_val, vwap=vwap_val, tech_signal=tech_signal,
        atm_iv=atm_iv, ivr=hist_summ.get("ivr"), ivp=hist_summ.get("ivp"),
        iv_skew_pct=iv_skew.get("skew_pct"), iv_skew_dir=iv_skew.get("direction", "N/A"),
    )


# ════════════════════════════════════════════════════════════════
#  Main loop
# ════════════════════════════════════════════════════════════════
def main():
    if "--gui" in sys.argv:
        config.DISPLAY_MODE = "tkinter"
    if "--terminal" in sys.argv:
        config.DISPLAY_MODE = "terminal"

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
    print(f"  NSE LIVE OI DASHBOARD  v5.3  [{SYMBOL}]")
    print(f"  Env: {'Colab' if IN_COLAB else 'Local'}  |  Lot: {LOT_SIZE}  |  Refresh: {REFRESH_RATE}s")
    print(f"  Mode: {'DEMO (markets closed)' if use_demo else 'LIVE'}")
    print(f"  4-vote: PCR + OI Score + Max Pain + RSI/VWAP")
    print(f"  v5.3 NEW: ATM IV · IVR · IVP · Daily IV · IV Skew (which side is heavy)")
    print(f"  IV history: {iv_history.filepath}  ({len(iv_history.history)} days loaded)")
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
                vix  = str(round(14.5 + math.sin(cycle * 0.5) * 2.5
                                 + random.uniform(-0.3, 0.3), 2))
                data = demo_data(SYMBOL, cycle)
            else:
                vix  = fetch_vix()
                data = fetch_chain(session, SYMBOL)
                if not data:   # catches None AND {} (Cloudflare empty block)
                    print(f"No data (NSE returned {type(data).__name__}). "
                          f"Next open: {next_open_str()}. Retrying in {REFRESH_RATE}s...")
                    if not is_market_open():
                        print("Switching to Demo Mode...")
                        use_demo = True
                    time.sleep(REFRESH_RATE)
                    continue
                if cycle % 10 == 0:
                    session = create_session()

            sig = process_cycle(data, SYMBOL, vix, use_demo, cycle)

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
