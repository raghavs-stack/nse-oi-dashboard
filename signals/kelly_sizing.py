# ════════════════════════════════════════════════════════════════
#  signals/kelly_sizing.py  — v5.10
#
#  Kelly Criterion position sizing + Exit signal engine
#  ──────────────────────────────────────────────────────────────────
#  Based on:
#    • Kelly (1956): "A New Interpretation of Information Rate"
#    • Half-Kelly (standard at hedge funds for safety)
#    • Thorp (1997): "The Kelly Criterion in Blackjack, Sports Betting
#      and the Stock Market"
#
#  Kelly formula:
#    f* = (p × b - q) / b
#    where:
#      p = P(win) = historical win rate
#      q = 1 - p  = P(loss)
#      b = avg_win / avg_loss  (reward/risk ratio)
#
#  Position sizing guidance:
#    f* > 0.40 → 3 lots (high edge)
#    f* > 0.25 → 2 lots
#    f* > 0.10 → 1 lot
#    f* ≤ 0.10 → paper trade only
#
#  Half-Kelly is used as standard safety (reduces drawdown significantly
#  while losing only ~10% of long-run growth rate vs full Kelly).
#
#  Exit Engine:
#    Four exit triggers for long option positions:
#      1. TARGET  — premium 2× entry (100% gain)
#      2. STOP    — premium 0.5× entry (50% loss)
#      3. IV_CRUSH — ATM IV spikes >30% since entry → exit before crush
#      4. THETA   — if DTE ≤ 2 and position OTM → forced time-decay exit
#      5. TRAIL   — once at +50%, trail stop to breakeven
# ════════════════════════════════════════════════════════════════

from __future__ import annotations
import glob
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ════════════════════════════════════════════════════════════════
#  1.  Kelly Criterion
# ════════════════════════════════════════════════════════════════
def _load_all_signals(symbol: str) -> pd.DataFrame:
    """Read all session CSV files and return taken-trades only."""
    rows = []
    for f in glob.glob(f"{symbol}_OI_*.csv"):
        try:
            df = pd.read_csv(f)
            taken = df[df.get("Taken", pd.Series(dtype=bool)).astype(str).str.lower() == "true"]
            rows.append(taken)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def kelly_sizing(
    symbol:      str,
    signal_log:  list = None,
    target_mult: float = 2.0,
    sl_mult:     float = 0.5,
    half_kelly:  bool  = True,
) -> dict:
    """
    Compute Kelly fraction and lot recommendation from historical trades.

    Priority:
      1. Use live signal_log (current session — most up-to-date)
      2. Fall back to CSV files (historical sessions)
      3. Fall back to theoretical defaults (new account)

    Returns dict with:
      win_rate:   float  (0–1)
      avg_win:    float  (average premium gain multiple)
      avg_loss:   float  (average premium loss multiple)
      kelly_f:    float  (Kelly fraction 0–1, HALF if half_kelly=True)
      lots:       int    (1, 2, or 3)
      confidence: str    ("HIGH" / "MEDIUM" / "LOW" / "DEFAULT")
      n_trades:   int
      note:       str
    """
    wins, losses = [], []

    # ── Live session data ─────────────────────────────────────────
    if signal_log:
        taken = [s for s in signal_log if getattr(s, "taken", False)
                 and getattr(s, "outcome", None)]
        for s in taken:
            if s.outcome == "WIN":
                wins.append(target_mult - 1)
            elif s.outcome == "LOSS":
                losses.append(1 - sl_mult)

    # ── CSV history ───────────────────────────────────────────────
    df_hist = _load_all_signals(symbol)
    if not df_hist.empty and "Score" in df_hist.columns:
        n_hist = len(df_hist)
        # Approximate: score >= 65 → win, else loss (proxy until outcome tracking)
        scores = pd.to_numeric(df_hist["Score"], errors="coerce").dropna()
        if len(scores) > 0:
            proxy_wins   = int((scores >= 65).sum())
            proxy_losses = len(scores) - proxy_wins
            # Only use if we don't have live data
            if not wins and not losses:
                wins   = [target_mult - 1] * proxy_wins
                losses = [1 - sl_mult]     * proxy_losses

    n_trades = len(wins) + len(losses)

    # ── Fallback defaults (academic baseline: 50% win, 2:1 R:R → K=25%) ─
    if n_trades < 10:
        return {
            "win_rate":   0.50,
            "avg_win":    target_mult - 1,
            "avg_loss":   1 - sl_mult,
            "kelly_f":    0.125 if half_kelly else 0.25,
            "lots":       1,
            "confidence": "DEFAULT",
            "n_trades":   n_trades,
            "note":       f"Insufficient history ({n_trades} trades). Using 50% win-rate baseline.",
        }

    p = len(wins) / n_trades
    q = 1 - p
    b = float(np.mean(wins)) / float(np.mean(losses)) if losses else (target_mult - 1) / (1 - sl_mult)

    if b <= 0 or p <= 0:
        kelly_f = 0.0
    else:
        kelly_f = max(0.0, (p * b - q) / b)

    if half_kelly:
        kelly_f = kelly_f / 2.0

    kelly_f = round(min(0.50, kelly_f), 3)   # cap at 50% of account

    if kelly_f > 0.25:
        lots = 3
    elif kelly_f > 0.15:
        lots = 2
    elif kelly_f > 0.05:
        lots = 1
    else:
        lots = 0

    if n_trades >= 30:
        confidence = "HIGH"
    elif n_trades >= 15:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "win_rate":   round(p, 3),
        "avg_win":    round(float(np.mean(wins)), 3) if wins else 0.0,
        "avg_loss":   round(float(np.mean(losses)), 3) if losses else 0.0,
        "kelly_f":    kelly_f,
        "lots":       lots,
        "confidence": confidence,
        "n_trades":   n_trades,
        "note": (f"Win rate={p:.0%}  b={b:.2f}  "
                 f"{'Half-' if half_kelly else ''}Kelly={kelly_f:.1%}  → {lots} lot(s)"),
    }


# ════════════════════════════════════════════════════════════════
#  2.  Open position tracker (for exit engine)
# ════════════════════════════════════════════════════════════════
@dataclass
class OpenPosition:
    """Tracks one open option buying position."""
    symbol:         str
    opt_type:       str       # CE | PE
    strike:         int
    entry_premium:  float
    entry_time:     str       # HH:MM
    entry_iv:       float     # ATM IV at entry
    entry_spot:     float
    dte_at_entry:   int
    lot_size:       int
    lots:           int       = 1

    # Runtime state (updated each cycle)
    current_premium: float    = 0.0
    peak_premium:    float    = 0.0
    trail_sl:        float    = 0.0
    trailing_active: bool     = False
    cycles_held:     int      = 0

    @property
    def pnl_pct(self) -> float:
        if self.entry_premium <= 0:
            return 0.0
        return (self.current_premium - self.entry_premium) / self.entry_premium * 100

    @property
    def pnl_rs(self) -> float:
        return (self.current_premium - self.entry_premium) * self.lot_size * self.lots


# ════════════════════════════════════════════════════════════════
#  3.  Exit Engine
# ════════════════════════════════════════════════════════════════
@dataclass
class ExitSignal:
    exit_now:  bool
    reason:    str
    exit_type: str    # TARGET | STOP | IV_CRUSH | THETA | TRAIL | HOLD
    pnl_pct:   float
    urgency:   str    # IMMEDIATE | SOON | MONITOR


def check_exit(
    pos:          OpenPosition,
    current_prem: float,
    current_iv:   float,
    dte_now:      int,
    target_mult:  float = 2.0,
    sl_mult:      float = 0.5,
    trail_trigger: float = 0.50,   # start trailing at +50%
    iv_crush_thr:  float = 0.30,   # exit if IV rose 30% since entry
) -> ExitSignal:
    """
    Check all exit conditions for an open option position.

    Call every cycle with updated premium, IV, and DTE.
    Returns ExitSignal with exit recommendation.

    Priority order:
      1. Hard stop-loss (immediate — capital protection)
      2. Target hit (take profit)
      3. IV crush threat (protect profit from vega)
      4. Theta-decay forced exit (DTE ≤ 2 + OTM)
      5. Trail stop (preserve locked-in gains)
    """
    pos.current_premium = current_prem
    pos.cycles_held     += 1
    pos.peak_premium    = max(pos.peak_premium, current_prem)

    target_prem = pos.entry_premium * target_mult
    sl_prem     = pos.entry_premium * sl_mult
    pnl_pct     = pos.pnl_pct

    # ── 1. Hard stop-loss ─────────────────────────────────────────
    if current_prem <= sl_prem:
        return ExitSignal(
            exit_now=True,
            reason=f"Stop-loss hit: prem {current_prem:.1f} ≤ SL {sl_prem:.1f} ({pnl_pct:+.0f}%)",
            exit_type="STOP", pnl_pct=pnl_pct, urgency="IMMEDIATE"
        )

    # ── 2. Target ─────────────────────────────────────────────────
    if current_prem >= target_prem:
        return ExitSignal(
            exit_now=True,
            reason=f"Target hit: prem {current_prem:.1f} ≥ Target {target_prem:.1f} ({pnl_pct:+.0f}%)",
            exit_type="TARGET", pnl_pct=pnl_pct, urgency="IMMEDIATE"
        )

    # ── 3. IV crush threat ─────────────────────────────────────────
    if pos.entry_iv > 0 and current_iv > 0:
        iv_rise_pct = (current_iv - pos.entry_iv) / pos.entry_iv
        if iv_rise_pct > iv_crush_thr and pnl_pct > 10:
            return ExitSignal(
                exit_now=True,
                reason=(f"IV crush risk: IV rose {iv_rise_pct:.0%} since entry "
                        f"(entry={pos.entry_iv:.1f}% now={current_iv:.1f}%). Exit before crush."),
                exit_type="IV_CRUSH", pnl_pct=pnl_pct, urgency="IMMEDIATE"
            )

    # ── 4. Theta forced exit (near-expiry OTM) ──────────────────────
    is_otm = (
        (pos.opt_type == "CE" and current_prem < pos.entry_premium * 0.6) or
        (pos.opt_type == "PE" and current_prem < pos.entry_premium * 0.6)
    )
    if dte_now <= 2 and is_otm:
        return ExitSignal(
            exit_now=True,
            reason=f"Theta exit: DTE={dte_now} and OTM — time value accelerating to zero",
            exit_type="THETA", pnl_pct=pnl_pct, urgency="SOON"
        )

    # ── 5. Trail stop ─────────────────────────────────────────────
    if pnl_pct >= trail_trigger * 100:
        if not pos.trailing_active:
            pos.trailing_active = True
            pos.trail_sl = max(pos.entry_premium, current_prem * 0.70)
        else:
            pos.trail_sl = max(pos.trail_sl, current_prem * 0.70)

    if pos.trailing_active and current_prem <= pos.trail_sl:
        return ExitSignal(
            exit_now=True,
            reason=(f"Trail stop hit: prem {current_prem:.1f} ≤ trail {pos.trail_sl:.1f} "
                    f"(peak was {pos.peak_premium:.1f}, locking in {pnl_pct:+.0f}%)"),
            exit_type="TRAIL", pnl_pct=pnl_pct, urgency="IMMEDIATE"
        )

    # ── HOLD ──────────────────────────────────────────────────────
    trail_note = (f" | Trail active: SL={pos.trail_sl:.1f}" if pos.trailing_active else "")
    return ExitSignal(
        exit_now=False,
        reason=f"Hold: {pnl_pct:+.0f}% | DTE={dte_now} | IV={current_iv:.1f}%{trail_note}",
        exit_type="HOLD", pnl_pct=pnl_pct, urgency="MONITOR"
    )
