# ════════════════════════════════════════════════════════════════
#  backtest/eod_backtest.py
#  Signal + TradeRec dataclasses and EOD backtest engine.
#  Only TAKEN trades are backtested (skipped signals excluded).
# ════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field
from typing import Optional
import os
import pandas as pd

import state
from config import SYMBOL, LOT_SIZE
from core.market_hours import now_ist


@dataclass
class TradeRec:
    label:    str       # Conservative / Moderate / Aggressive
    strike:   int
    opt_type: str       # CE or PE
    premium:  float
    sl:       float
    target:   float
    lot_cost: float
    rr:       str
    reason:   str


@dataclass
class Signal:
    time:            str
    bias:            str
    spot:            float
    pcr:             float
    rec1:            TradeRec
    rec2:            TradeRec
    rec3:            TradeRec
    score:           int   = 0
    taken:           bool  = False
    skip_reason:     str   = ""
    votes_unanimous: bool  = False
    rsi:             Optional[float] = None
    vwap:            Optional[float] = None
    tech_signal:     str   = "NEUTRAL"
    spot_exit:       Optional[float] = None
    outcome:         Optional[str]   = None
    # v5.3 IV fields
    atm_iv:          Optional[float] = None
    ivr:             Optional[float] = None
    ivp:             Optional[float] = None
    iv_skew_pct:     Optional[float] = None
    iv_skew_dir:     str             = "N/A"


def run_eod_backtest(final_spot: float):
    """
    Delta-approximation backtest on today's taken trades.
    Brokerage: ~Rs40 per lot per leg.
    """
    taken_sigs   = [s for s in state.signal_log if s.taken]
    skipped_sigs = [s for s in state.signal_log if not s.taken]

    W = 72
    print("\n" + "=" * W)
    print("  EOD BACKTEST REPORT  (TAKEN TRADES ONLY)")
    print(f"  Total signals: {len(state.signal_log)}  |  "
          f"Taken: {len(taken_sigs)}  |  Skipped: {len(skipped_sigs)}  "
          f"|  Brokerage saved: ~Rs{len(skipped_sigs) * 3 * 40:,} est.")
    print("=" * W)

    if not taken_sigs:
        print("  No trades were taken today (all signals below quality gate).")
        print("=" * W)
        return

    deltas = {
        "Conservative (ATM)": 0.50,
        "Moderate (1-OTM)":   0.35,
        "Aggressive (2-OTM)": 0.20,
    }
    rows, total_pnl = [], 0
    for sig in taken_sigs:
        spot_move = final_spot - sig.spot
        for rec in [sig.rec1, sig.rec2, sig.rec3]:
            delta     = deltas.get(rec.label, 0.35)
            direction = 1 if rec.opt_type == "CE" else -1
            opt_move  = delta * spot_move * direction
            if opt_move <= -(rec.premium - rec.sl):
                pnl_pts, exit_r = -(rec.premium - rec.sl), "SL Hit"
            elif opt_move >= (rec.target - rec.premium):
                pnl_pts, exit_r = rec.target - rec.premium, "Target Hit"
            else:
                pnl_pts, exit_r = opt_move, "EOD Exit"
            pnl_rs    = round(pnl_pts * LOT_SIZE - 40, 0)
            total_pnl += pnl_rs
            rows.append({
                "Time":   sig.time, "Score": sig.score, "Bias": sig.bias,
                "Trade":  f"{rec.strike} {rec.opt_type}", "Type": rec.label.split(" ")[0],
                "Entry":  rec.premium, "Exit": exit_r,
                "PnL_Rs": pnl_rs, "Result": "WIN" if pnl_rs > 0 else "LOSS",
                "ATM_IV": sig.atm_iv, "IVR": sig.ivr, "IVP": sig.ivp,
                "IV_Skew": sig.iv_skew_pct, "IV_SkewDir": sig.iv_skew_dir,
            })

    df   = pd.DataFrame(rows)
    wins = (df["PnL_Rs"] > 0).sum()
    loss = (df["PnL_Rs"] <= 0).sum()

    print(f"  Spot range: Rs{taken_sigs[0].spot:,.2f} – Rs{taken_sigs[-1].spot:,.2f}"
          f"   Final: Rs{final_spot:,.2f}")
    print("=" * W)
    hdr = (f"  {'Time':<7} {'Sc':>4} {'Bias':<9} {'Trade':<13} "
           f"{'Type':<16} {'Entry':>7} {'Exit':<14} {'P&L (net)':>10}  Result")
    print(hdr)
    print("  " + "-" * (W - 2))
    for _, r in df.iterrows():
        print(f"  {r['Time']:<7} {r['Score']:>4} {r['Bias']:<9} {r['Trade']:<13} "
              f"{r['Type']:<16} Rs{r['Entry']:>5.1f}  {r['Exit']:<14} "
              f"Rs{r['PnL_Rs']:>+7,.0f}  {'+'if r['PnL_Rs']>0 else '-'}")
    print("=" * W)
    icon = "NET GAIN" if total_pnl > 0 else "NET LOSS"
    print(f"  {icon}: Rs{total_pnl:+,.0f}  (after ~Rs40/lot brokerage per leg)")
    print(f"  Win Rate: {wins/(wins+loss):.0%}  |  Wins: {wins}  |  Losses: {loss}")

    if skipped_sigs:
        print("-" * W)
        print(f"  SKIPPED SIGNALS ({len(skipped_sigs)}):")
        for s in skipped_sigs:
            print(f"  {s.time}  Score:{s.score:>3}  {s.bias:<9}  {s.skip_reason}")

    print("=" * W)
    bt_file = f"{SYMBOL}_backtest_{now_ist().strftime('%Y%m%d')}.csv"
    df.to_csv(bt_file, index=False)
    print(f"  Saved → {bt_file}")
    print("=" * W)
