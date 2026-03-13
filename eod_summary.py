#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════
#  eod_summary.py  — NSE OI Dashboard v5.6
#
#  End-of-Day report: market narrative, signal quality, trade P&L.
#  Reads today's CSV log and (if present) today's backtest CSV.
#
#  Usage:
#    python eod_summary.py                      # NIFTY (default)
#    python eod_summary.py --symbol BANKNIFTY   # BANKNIFTY
#    python eod_summary.py --date 20260310      # specific date
#    python eod_summary.py --save               # also save report to .txt
#    python eod_summary.py --both               # NIFTY + BANKNIFTY
# ════════════════════════════════════════════════════════════════

import sys, os, math, re
from datetime import datetime, date
from pathlib import Path

import pandas as pd

# ── CLI args ──────────────────────────────────────────────────────
symbol  = "NIFTY"
if "--symbol" in sys.argv:
    idx = sys.argv.index("--symbol")
    if idx + 1 < len(sys.argv):
        symbol = sys.argv[idx + 1].upper()

date_str = datetime.now().strftime("%Y%m%d")
if "--date" in sys.argv:
    idx = sys.argv.index("--date")
    if idx + 1 < len(sys.argv):
        date_str = sys.argv[idx + 1]

SAVE_REPORT = "--save" in sys.argv
RUN_BOTH    = "--both" in sys.argv

LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 40, "MIDCPNIFTY": 75}  # NSE Jan-2026

# ════════════════════════════════════════════════════════════════
#  Core analysis function
# ════════════════════════════════════════════════════════════════
def analyse(symbol: str, date_str: str) -> list[str]:
    """
    Return a list of formatted lines for the EOD report.
    """
    lot_size  = LOT_SIZES.get(symbol, 65)
    log_file  = f"{symbol}_OI_{date_str}.csv"
    bt_file   = f"{symbol}_backtest_{date_str}.csv"
    report_dt = datetime.strptime(date_str, "%Y%m%d").strftime("%d %b %Y")

    lines = []
    W     = 72

    def bar(char="═"): lines.append(char * W)
    def hdr(txt):      lines.append(f"  {txt}")
    def sub(txt):      lines.append(f"    {txt}")
    def blank():       lines.append("")

    # ── Load CSV ──────────────────────────────────────────────────
    if not Path(log_file).exists():
        bar(); hdr(f"EOD SUMMARY — {symbol}  [{report_dt}]"); bar()
        hdr(f"No data file found: {log_file}")
        hdr(f"Make sure you ran:  python main.py --symbol {symbol}")
        bar(); return lines

    df = pd.read_csv(log_file)
    if df.empty:
        bar(); hdr(f"EOD SUMMARY — {symbol}  [{report_dt}]"); bar()
        hdr("Log file is empty — no cycles recorded today."); bar()
        return lines

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"]).sort_values("Timestamp").reset_index(drop=True)
    df["ATM_IV"] = pd.to_numeric(df["ATM_IV"], errors="coerce")
    df["IVR"]    = pd.to_numeric(df["IVR"],    errors="coerce")
    df["IVP"]    = pd.to_numeric(df["IVP"],    errors="coerce")

    first   = df.iloc[0]
    last    = df.iloc[-1]
    n_rows  = len(df)
    taken   = df[df["Taken"].astype(str).str.lower() == "true"]
    skipped = df[df["Taken"].astype(str).str.lower() != "true"]
    session_start = first["Timestamp"].strftime("%H:%M")
    session_end   = last["Timestamp"].strftime("%H:%M")

    spot_open  = float(first["Spot"])
    spot_close = float(last["Spot"])
    spot_high  = float(df["Spot"].max())
    spot_low   = float(df["Spot"].min())
    spot_chg   = spot_close - spot_open
    spot_chg_p = (spot_chg / spot_open) * 100

    vix_vals   = pd.to_numeric(df["VIX"], errors="coerce").dropna()
    vix_open   = float(vix_vals.iloc[0])  if len(vix_vals) else None
    vix_close  = float(vix_vals.iloc[-1]) if len(vix_vals) else None

    # ── Header ────────────────────────────────────────────────────
    bar()
    hdr(f"  EOD SUMMARY — {symbol}  [{report_dt}]  |  Generated: {datetime.now().strftime('%H:%M IST')}")
    bar()

    # ── 1. Session overview ───────────────────────────────────────
    blank()
    hdr("1. SESSION OVERVIEW")
    hdr("─" * (W - 2))
    sub(f"Session:     {session_start} → {session_end}  ({n_rows} cycles / ~{int(n_rows*35/60)} min of data)")
    sub(f"Open:        ₹{spot_open:>10,.2f}")
    sub(f"High:        ₹{spot_high:>10,.2f}")
    sub(f"Low:         ₹{spot_low:>10,.2f}")
    sub(f"Close:       ₹{spot_close:>10,.2f}")
    direction = "▲" if spot_chg >= 0 else "▼"
    sub(f"Net move:    {direction} {abs(spot_chg):,.2f} pts  ({spot_chg_p:+.2f}%)  Range: {spot_high - spot_low:.0f} pts")
    if vix_open and vix_close:
        vix_dir = "▲" if vix_close >= vix_open else "▼"
        sub(f"India VIX:   {vix_open:.2f} → {vix_close:.2f}  ({vix_dir} {abs(vix_close-vix_open):.2f})")

    # ── 2. Market narrative ───────────────────────────────────────
    blank()
    hdr("2. MARKET NARRATIVE")
    hdr("─" * (W - 2))

    bias_counts = df["Bias"].value_counts().to_dict()
    dominant = max(bias_counts, key=bias_counts.get)
    dominant_pct = bias_counts[dominant] / n_rows * 100

    sub(f"Dominant bias:  {dominant} ({dominant_pct:.0f}% of cycles)")
    sub(f"Bias breakdown: " + "  |  ".join(f"{k}: {v}" for k,v in sorted(bias_counts.items())))

    # PCR narrative
    pcr_vals = pd.to_numeric(df["PCR_Local"], errors="coerce").dropna()
    if not pcr_vals.empty:
        pcr_open  = float(pcr_vals.iloc[0])
        pcr_close = float(pcr_vals.iloc[-1])
        pcr_avg   = float(pcr_vals.mean())
        pcr_min   = float(pcr_vals.min())
        pcr_max   = float(pcr_vals.max())
        sub(f"PCR range:      {pcr_min:.3f} → {pcr_max:.3f}  (avg {pcr_avg:.3f}  |  open {pcr_open:.3f}  close {pcr_close:.3f})")
        if pcr_close > pcr_open + 0.1:
            sub(f"PCR trend:      Put OI building through session → protective/bearish positioning increased")
        elif pcr_close < pcr_open - 0.1:
            sub(f"PCR trend:      Call OI building through session → overhead resistance built up")
        else:
            sub(f"PCR trend:      Stable through session — balanced OI positioning")

    # Score narrative
    score_vals = pd.to_numeric(df["Score"], errors="coerce").dropna()
    if not score_vals.empty:
        score_avg = float(score_vals.mean())
        score_max = float(score_vals.max())
        score_min = float(score_vals.min())
        sub(f"Signal score:   avg {score_avg:.0f}  |  peak {score_max:.0f}  |  low {score_min:.0f}  (threshold: 55)")
        if score_max >= 55:
            sub(f"              → Score crossed threshold {(score_vals >= 55).sum()} time(s) — trade quality gate triggered")
        elif score_max >= 48:
            sub(f"              → Score peaked at {score_max:.0f} — near threshold. Strong directional session but scoring calibration may have suppressed signal.")
        else:
            sub(f"              → Score never approached 55 today — genuinely low conviction session")

    # Max pain & key levels
    if "MaxPain" in df.columns:
        mp_vals = pd.to_numeric(df["MaxPain"], errors="coerce").dropna()
        if not mp_vals.empty:
            mp_open  = float(mp_vals.iloc[0])
            mp_close = float(mp_vals.iloc[-1])
            sub(f"Max Pain:       ₹{mp_open:,.0f} (open) → ₹{mp_close:,.0f} (close)")
            if abs(spot_close - mp_close) <= 100:
                sub(f"              → Spot settled NEAR max pain (₹{mp_close:,.0f}) — options writers in control")
            elif spot_close > mp_close:
                sub(f"              → Spot closed ₹{spot_close - mp_close:.0f} ABOVE max pain — bullish bias held")
            else:
                sub(f"              → Spot closed ₹{mp_close - spot_close:.0f} BELOW max pain — bearish pressure persisted")

    if "Resistance" in df.columns and "Support" in df.columns:
        res_last = float(last["Resistance"]) if pd.notna(last["Resistance"]) else None
        sup_last = float(last["Support"])    if pd.notna(last["Support"])    else None
        if res_last and sup_last:
            sub(f"Key levels:     Resistance ₹{res_last:,.0f}  |  Support ₹{sup_last:,.0f}  (by close)")
            # Only call a breakout if the level was actually relevant to price action.
            # If resistance has drifted far below spot (bearish drift) or support far above,
            # those are intraday OI shifts — not structural breakouts.
            _step = 100 if symbol == "BANKNIFTY" else 50
            _res_above = res_last > spot_close + _step   # resistance is overhead
            _sup_below = sup_last < spot_close - _step   # support is below
            if _res_above and spot_close >= res_last:
                sub(f"              → Spot BROKE ABOVE resistance ₹{res_last:,.0f} — bullish breakout")
            elif _sup_below and spot_close <= sup_last:
                sub(f"              → Spot BROKE BELOW support ₹{sup_last:,.0f} — bearish breakdown")
            elif not _res_above and spot_close > res_last:
                sub(f"              → Resistance ₹{res_last:,.0f} shifted below spot — OI wall moved with price (bearish drift)")
            elif not _sup_below and spot_close < sup_last:
                sub(f"              → Support ₹{sup_last:,.0f} shifted above spot — OI wall moved with price (bullish drift)")
            else:
                sub(f"              → Spot traded WITHIN range all day — no clean breakout")

    # ── 3. IV analytics summary ───────────────────────────────────
    blank()
    hdr("3. IMPLIED VOLATILITY  (IV)")
    hdr("─" * (W - 2))
    iv_vals = df["ATM_IV"].dropna()
    if not iv_vals.empty:
        iv_open  = float(iv_vals.iloc[0])
        iv_close = float(iv_vals.iloc[-1])
        iv_high  = float(iv_vals.max())
        iv_low   = float(iv_vals.min())
        iv_avg   = float(iv_vals.mean())
        iv_chg   = iv_close - iv_open
        iv_dir   = "expanded ▲" if iv_chg > 0.5 else ("contracted ▼" if iv_chg < -0.5 else "stable →")
        sub(f"ATM IV:   {iv_open:.2f}% → {iv_close:.2f}%  ({iv_dir})  |  Range: {iv_low:.2f}%–{iv_high:.2f}%  Avg: {iv_avg:.2f}%")
        if iv_chg > 2:
            sub(f"          → Significant IV expansion (+{iv_chg:.1f}%) — fear/uncertainty increased during session")
        elif iv_chg < -2:
            sub(f"          → IV crushed (-{abs(iv_chg):.1f}%) — expected move event resolved, vol sellers profited")

        ivr_vals = df["IVR"].dropna()
        ivp_vals = df["IVP"].dropna()
        if not ivr_vals.empty:
            ivr_close = float(ivr_vals.iloc[-1])
            ivp_close = float(ivp_vals.iloc[-1]) if not ivp_vals.empty else None
            ivp_str = f"{ivp_close:.1f}" if ivp_close else "N/A"
            sub(f"IVR:      {ivr_close:.1f}  |  IVP: {ivp_str}")
            if ivr_close >= 50:
                sub(f"          → HIGH IV environment — premium selling strategies had structural edge today")
            elif ivr_close <= 25:
                sub(f"          → LOW IV environment — directional buying strategies had structural edge today")
            else:
                sub(f"          → Normal IV range — no strong edge for buyers or sellers from volatility alone")

        if "IV_Skew_Dir" in df.columns:
            skew_last = str(last.get("IV_Skew_Dir", "N/A"))
            skew_pct  = float(last["IV_Skew_Pct"]) if pd.notna(last.get("IV_Skew_Pct")) else None
            if skew_pct is not None:
                sub(f"IV Skew:  {skew_pct:+.2f}%  [{skew_last}]  "
                    f"{'→ Puts costlier — hedging demand / bearish sentiment' if skew_last == 'PUT HEAVY' else ('→ Calls costlier — upside chase / bullish positioning' if skew_last == 'CALL HEAVY' else '→ Balanced put/call pricing')}")
    else:
        sub("ATM IV data not available (market may have been closed during session).")

    # ── 4. Signal quality ─────────────────────────────────────────
    blank()
    hdr("4. SIGNAL QUALITY")
    hdr("─" * (W - 2))
    sub(f"Total cycles:   {n_rows}")
    sub(f"Signals taken:  {len(taken)}")
    sub(f"Signals skipped:{len(skipped)}")

    if not skipped.empty:
        sub(f"Skip reasons:")
        reasons = skipped["SkipReason"].value_counts()
        # Collapse all "Too soon" variants into one summary line
        too_soon_total = sum(cnt for r, cnt in reasons.items() if r.startswith("Too soon"))
        # Collapse all "Opening gate" variants into one summary line
        open_gate_total = sum(cnt for r, cnt in reasons.items() if r.startswith("Opening gate"))
        # Cap individual score-based reasons to top 12 — avoids 38-row lists
        score_reasons = [(r, c) for r, c in reasons.items()
                         if r.startswith("Score")]
        other_reasons = [(r, c) for r, c in reasons.items()
                         if not r.startswith("Too soon")
                         and not r.startswith("Opening gate")
                         and not r.startswith("Score")]
        TOP_SCORE_ROWS = 12
        printed_too_soon = False
        printed_gate     = False
        score_printed    = 0
        score_remainder_cnt = sum(c for _, c in score_reasons[TOP_SCORE_ROWS:])
        for reason, cnt in reasons.items():
            if reason.startswith("Too soon"):
                if not printed_too_soon and too_soon_total > 0:
                    sub(f"  {too_soon_total:>3}x  Too soon after last trade (within min gap)")
                    printed_too_soon = True
                continue
            if reason.startswith("Opening gate"):
                if not printed_gate and open_gate_total > 0:
                    sub(f"  {open_gate_total:>3}x  Opening gate (noise filter)")
                    printed_gate = True
                continue
            if reason.startswith("Score"):
                if score_printed < TOP_SCORE_ROWS:
                    sub(f"  {cnt:>3}x  {reason}")
                    score_printed += 1
                    if score_printed == TOP_SCORE_ROWS and score_remainder_cnt > 0:
                        sub(f"        … {score_remainder_cnt} more cycles in other score buckets")
                continue
            sub(f"  {cnt:>3}x  {reason}")

    # Score distribution bucketing
    if not score_vals.empty:
        buckets = {
            "< 40 (weak)":    (score_vals < 40).sum(),
            "40–54 (moderate)":(((score_vals >= 40) & (score_vals < 55)).sum()),
            "55–69 (good)":   (((score_vals >= 55) & (score_vals < 70)).sum()),
            "70+ (strong)":   (score_vals >= 70).sum(),
        }
        sub(f"Score distribution:")
        for bucket, cnt in buckets.items():
            bar_len = int(cnt / n_rows * 20)
            sub(f"  {bucket:<22} {'█' * bar_len} {cnt}")

    # ── 5. Trade details & P&L ────────────────────────────────────
    blank()
    hdr("5. TRADE DETAILS & P&L")
    hdr("─" * (W - 2))

    if taken.empty:
        sub("No trades were taken today.")
        sub("All signals were below the quality gate (score < 55) or trade cap reached.")
        # Show what would have happened to the best signal
        if not skipped.empty:
            # BUG-08 fix: only pick best among actually-skipped rows
            _skipped_scores = pd.to_numeric(skipped["Score"], errors="coerce").dropna()
            if not _skipped_scores.empty:
                best = skipped.loc[_skipped_scores.idxmax()]
                sub(f"")
                sub(f"Best missed signal:  {best['Timestamp'].strftime('%H:%M')}  "
                    f"Score:{int(best['Score'])}  {best['Bias']}  Spot:₹{float(best['Spot']):,.2f}")
                sub(f"  Recs: {best['Rec1']}  |  {best['Rec2']}  |  {best['Rec3']}")
    else:
        # Parse Rec columns: format is  STRIKETYPE@PREMIUM  e.g. 24200CE@63.5
        def parse_rec(rec_str):
            """Parse '24200CE@63.5' → (strike, opt_type, premium)"""
            m = re.match(r"(\d+)(CE|PE)@([\d.]+)", str(rec_str))
            if m:
                return int(m.group(1)), m.group(2), float(m.group(3))
            return None, None, None

        total_pnl    = 0
        all_trade_rows = []

        for _, sig in taken.iterrows():
            sig_time  = sig["Timestamp"].strftime("%H:%M")
            sig_spot  = float(sig["Spot"])
            sig_bias  = sig["Bias"]
            sig_score = int(sig["Score"])
            sig_iv    = f"{float(sig['ATM_IV']):.2f}%" if pd.notna(sig.get("ATM_IV")) else "N/A"
            sig_ivr   = f"{float(sig['IVR']):.1f}"    if pd.notna(sig.get("IVR"))    else "N/A"

            sub(f"{'─'*60}")
            sub(f"Signal @ {sig_time}   Score:{sig_score}   {sig_bias}   "
                f"Spot:₹{sig_spot:,.2f}   ATM-IV:{sig_iv}   IVR:{sig_ivr}")

            # Reconstruct spot at EOD for P&L (use last row spot)
            exit_spot = spot_close

            for rec_col, label in [("Rec1", "Conservative"), ("Rec2", "Moderate"), ("Rec3", "Aggressive")]:
                strike, opt_type, entry = parse_rec(sig.get(rec_col))
                if strike is None:
                    continue

                # Delta-approximation P&L
                deltas = {"Conservative": 0.50, "Moderate": 0.35, "Aggressive": 0.20}
                delta  = deltas[label]
                direction = 1 if opt_type == "CE" else -1
                sl     = round(entry * 0.50, 1)    # 50% of premium
                target = round(entry * 2.0,  1)    # 100% gain

                spot_move = exit_spot - sig_spot
                opt_move  = delta * spot_move * direction

                if opt_move <= -(entry - sl):
                    exit_price = sl
                    exit_tag   = "SL Hit"
                    pnl_pts    = -(entry - sl)
                elif opt_move >= (target - entry):
                    exit_price = target
                    exit_tag   = "Target Hit"
                    pnl_pts    = target - entry
                else:
                    exit_price = round(entry + opt_move, 1)
                    exit_tag   = "EOD Exit"
                    pnl_pts    = opt_move

                brokerage = 40  # ~Rs40/lot round-trip
                pnl_rs    = round(pnl_pts * lot_size - brokerage, 0)
                total_pnl += pnl_rs
                result    = "WIN ✅" if pnl_rs > 0 else "LOSS ❌"

                sub(f"  {label:<14}  {strike} {opt_type}  "
                    f"Entry:₹{entry:>6.1f}  Exit:₹{exit_price:>6.1f}  [{exit_tag:<11}]  "
                    f"P&L: ₹{pnl_rs:>+6,.0f}  {result}")

                all_trade_rows.append({
                    "Time": sig_time, "Score": sig_score, "Bias": sig_bias,
                    "Trade": f"{strike}{opt_type}", "Label": label,
                    "Entry": entry, "Exit": exit_price, "ExitReason": exit_tag,
                    "PnL_Rs": pnl_rs, "Result": "WIN" if pnl_rs > 0 else "LOSS",
                    "ATM_IV": sig_iv, "IVR": sig_ivr,
                })

        if all_trade_rows:
            blank()
            df_trades = pd.DataFrame(all_trade_rows)
            wins  = (df_trades["PnL_Rs"] > 0).sum()
            total = len(df_trades)
            sub(f"{'═'*60}")
            icon = "NET PROFIT 🟢" if total_pnl > 0 else "NET LOSS 🔴"
            sub(f"{icon}:  ₹{total_pnl:+,.0f}  (after ~₹40/lot brokerage)")
            sub(f"Win rate:  {wins}/{total}  ({wins/total*100:.0f}%)")
            sub(f"Per lot:   ₹{total_pnl/max(total,1):+,.0f} avg")

    # ── 6. What-if: best skipped signal ──────────────────────────
    if not skipped.empty and len(taken) == 0:
        pass   # already shown above
    elif not skipped.empty:
        blank()
        hdr("6. WHAT-IF: BEST SKIPPED SIGNAL")
        hdr("─" * (W - 2))
        # BUG-09 fix: score_vals.dropna() may not contain all skipped indices
        _skipped_score2 = pd.to_numeric(skipped["Score"], errors="coerce").dropna()
        best_skip_idx   = _skipped_score2.idxmax() if not _skipped_score2.empty else None
        if best_skip_idx is not None:
            best = df.loc[best_skip_idx]
            sub(f"Highest-score skipped: {best['Timestamp'].strftime('%H:%M')}  "
                f"Score:{int(best['Score'])}  {best['Bias']}  Spot:₹{float(best['Spot']):,.2f}")
            sub(f"Reason skipped:  {best['SkipReason']}")
            sub(f"Recommendations: {best['Rec1']}  |  {best['Rec2']}  |  {best['Rec3']}")

    # ── 7. Session character summary ─────────────────────────────
    blank()
    hdr("7. SESSION CHARACTER")
    hdr("─" * (W - 2))

    # Trend type
    spot_first_half  = df.iloc[:n_rows//2]["Spot"].mean()
    spot_second_half = df.iloc[n_rows//2:]["Spot"].mean()

    if abs(spot_chg) < (spot_high - spot_low) * 0.2:
        session_type = "RANGE-BOUND 🔄 (opened and closed near same level)"
    elif spot_chg > 0 and spot_first_half < spot_second_half:
        session_type = "TRENDING UP 📈 (steady upward drift through the day)"
    elif spot_chg < 0 and spot_first_half > spot_second_half:
        session_type = "TRENDING DOWN 📉 (steady downward drift through the day)"
    elif spot_chg > 0 and spot_first_half > spot_second_half:
        session_type = "V-SHAPE REVERSAL 🔼 (sold off then recovered strongly)"
    elif spot_chg < 0 and spot_first_half < spot_second_half:
        session_type = "INVERTED V 🔽 (rallied then sold off sharply)"
    else:
        session_type = "CHOPPY 〰 (no clear directional structure)"

    sub(f"Session type:   {session_type}")

    # VIX interpretation
    if vix_close:
        if vix_close < 14:
            vix_regime = "LOW VIX — complacent market, tight ranges expected to continue"
        elif vix_close < 18:
            vix_regime = "NORMAL VIX — standard sizing appropriate"
        elif vix_close < 24:
            vix_regime = "ELEVATED VIX — wider ranges, reduce position size"
        else:
            vix_regime = "HIGH VIX — volatile conditions, options premium expensive"
        sub(f"VIX regime:     {vix_close:.2f} — {vix_regime}")

    # Tomorrow's watch
    blank()
    hdr("8. LEVELS TO WATCH TOMORROW")
    hdr("─" * (W - 2))
    step    = 100 if symbol == "BANKNIFTY" else 50
    atm     = round(spot_close / step) * step
    mp_last = float(df["MaxPain"].dropna().iloc[-1]) if "MaxPain" in df.columns and not df["MaxPain"].dropna().empty else None

    # Use forward-looking unconstrained max-OI strikes (added in v5.7).
    # These represent where CE/PE writers have the most exposure heading into tomorrow,
    # regardless of where spot closed. Fall back to intraday Resistance/Support for
    # legacy CSVs that don't have CE_MaxOI_Strike.
    if "CE_MaxOI_Strike" in df.columns and pd.notna(last.get("CE_MaxOI_Strike")):
        res_eod = float(last["CE_MaxOI_Strike"])
        sup_eod = float(last["PE_MaxOI_Strike"])
        levels_source = "max total CE/PE OI — closing writer positions"
    elif "Resistance" in df.columns and pd.notna(last.get("Resistance")):
        res_eod = float(last["Resistance"])
        sup_eod = float(last["Support"])
        levels_source = "intraday OI wall (legacy)"
    else:
        res_eod = sup_eod = None
        levels_source = ""

    if res_eod and sup_eod:
        # Validate: resistance should be above spot, support below — swap if drifted
        if res_eod < spot_close and sup_eod < spot_close:
            # Both below spot (typical bearish drift day): resistance = higher of the two
            res_eod, sup_eod = max(res_eod, sup_eod), min(res_eod, sup_eod)
        elif res_eod > spot_close and sup_eod > spot_close:
            # Both above spot (typical bullish drift day): support = lower of the two
            res_eod, sup_eod = max(res_eod, sup_eod), min(res_eod, sup_eod)

        sub(f"Resistance:   ₹{res_eod:>9,.0f}  ({levels_source})")
        sub(f"Support:      ₹{sup_eod:>9,.0f}  ({levels_source})")
        if mp_last:  sub(f"Max Pain:     ₹{mp_last:>9,.0f}  (options expiry gravitational pull)")
        sub(f"ATM strike:   ₹{atm:>9,.0f}  (nearest ₹{step} strike to close ₹{spot_close:,.0f})")

        # Bull / Bear targets: must be on the correct side of spot
        bull_base = res_eod if res_eod > spot_close else spot_close
        bear_base = sup_eod if sup_eod < spot_close else spot_close
        sub(f"Bull target:  ₹{bull_base + step*2:>9,.0f}  if resistance breaks")
        sub(f"Bear target:  ₹{bear_base - step*2:>9,.0f}  if support breaks")
    else:
        if mp_last:  sub(f"Max Pain:     ₹{mp_last:>9,.0f}  (options expiry gravitational pull)")
        sub(f"ATM strike:   ₹{atm:>9,.0f}  (nearest ₹{step} strike to close ₹{spot_close:,.0f})")
        sub(f"Bull target:  ₹{spot_close + step*4:>9,.0f}  (spot + 4 steps)")
        sub(f"Bear target:  ₹{spot_close - step*4:>9,.0f}  (spot - 4 steps)")

    # ── Footer ────────────────────────────────────────────────────
    blank()
    bar()
    hdr(f"  Log: {log_file}  |  Symbol: {symbol}  |  Lot: {lot_size}  |  v5.7")
    bar()
    return lines


# ════════════════════════════════════════════════════════════════
#  Output
# ════════════════════════════════════════════════════════════════
def print_and_optionally_save(lines: list[str], symbol: str):
    text = "\n".join(lines)
    print(text)
    if SAVE_REPORT:
        out = f"{symbol}_eod_report_{date_str}.txt"
        Path(out).write_text(text)
        print(f"\n  📄 Saved → {out}")


if RUN_BOTH:
    for sym in ["NIFTY", "BANKNIFTY"]:
        lines = analyse(sym, date_str)
        print_and_optionally_save(lines, sym)
        print()
else:
    lines = analyse(symbol, date_str)
    print_and_optionally_save(lines, symbol)
