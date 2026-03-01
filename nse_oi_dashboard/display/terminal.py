# ════════════════════════════════════════════════════════════════
#  display/terminal.py
#  Non-scrolling terminal / Colab display.
#  v5.3: adds IV section (ATM IV, IVR, IVP, daily IV, skew).
# ════════════════════════════════════════════════════════════════

import os
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

import state
from config import (
    SYMBOL, LOT_SIZE, REFRESH_RATE,
    LOCALIZED_PCR_RANGE, PCR_THRESHOLDS,
    RSI_PERIOD, VWAP_WINDOW, MAX_TRADES_PER_DAY,
    PLOT_FILE,
)
from core.market_hours import now_ist, next_open_str, is_market_open
from core.nse_fetcher import nearest_strike

# ── Environment detection ─────────────────────────────────────────
try:
    import google.colab
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

try:
    from IPython.display import clear_output, display as ipy_display
    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

if not IN_COLAB:
    try:
        matplotlib.use("Agg")
    except Exception:
        pass
SAVE_PLOT = not IN_COLAB


# ════════════════════════════════════════════════════════════════
def render(df, symbol, spot, vix, expiry, pcr, bias,
           max_pain, resistance, support, net_score,
           roc_alerts, recs, demo, cycle,
           score=0, score_breakdown=None, taken=False,
           skip_reason="", unanimous=False,
           rsi=None, vwap=None, tech_signal="NEUTRAL",
           pcr_signal="NEUTRAL", pcr_signal_color="#f39c12",
           local_pcr=None,
           iv_data: dict = None):
    """
    Atomic screen update — builds full output then clears and prints at once.
    In tkinter mode this is a no-op (OITkApp handles its own rendering).

    iv_data (v5.3): dict with keys:
      atm_iv, ivr, ivp, skew, daily, hist_summary
    """
    from config import DISPLAY_MODE
    if DISPLAY_MODE == "tkinter":
        return

    W   = 68
    SEP = "=" * W
    THN = "-" * W
    mode_tag  = "DEMO" if demo else "LIVE"
    bias_icon = {"BULLISH": "[B+]", "BEARISH": "[B-]", "NEUTRAL": "[--]"}.get(bias, "")

    L = []
    a = L.append

    # ── Header ───────────────────────────────────────────────────
    a(SEP)
    a(f"  {symbol} OI DASHBOARD v5.3 [{mode_tag}]  "
      f"{now_ist().strftime('%H:%M:%S IST')}  Cycle #{cycle}")
    a(f"  Spot: Rs{spot:,.2f}  |  VIX: {vix}  |  Expiry: {expiry}  |  Lot: {LOT_SIZE}")
    a(SEP)

    # ── Market summary ────────────────────────────────────────────
    local_pcr_str = f"{local_pcr:.3f}" if local_pcr is not None else "N/A"
    a(f"  PCR(full): {pcr:.3f}  |  PCR(local±{LOCALIZED_PCR_RANGE}): {local_pcr_str}")
    a(f"  5-Level Signal: [{pcr_signal}]  |  {bias_icon} BIAS: {bias}  |  Wtd OI Score: {net_score:+,}")
    a(f"  Max Pain: Rs{int(max_pain):,}  |  "
      f"Resistance (max ΔCE): Rs{resistance:,}  |  "
      f"Support (max ΔPE): Rs{support:,}")

    # RSI + VWAP
    rsi_str  = f"{rsi:.1f}"   if rsi  is not None else "warming..."
    vwap_str = f"Rs{vwap:.1f}" if vwap is not None else "warming..."
    tech_icon = {"BULLISH": "[B+]", "BEARISH": "[B-]", "NEUTRAL": "[--]"}.get(tech_signal, "")
    a(f"  RSI({RSI_PERIOD}): {rsi_str:<10}  VWAP: {vwap_str:<12}  "
      f"Tech Signal: {tech_icon} {tech_signal}"
      f"{'  (needs ' + str(RSI_PERIOD+1) + ' cycles)' if rsi is None else ''}")
    a(THN)

    # ── v5.3 IV Panel ─────────────────────────────────────────────
    if iv_data:
        from signals.iv_analytics import format_iv_panel
        iv_lines = format_iv_panel(
            iv_data.get("skew",        {}),
            iv_data.get("daily",       {}),
            iv_data.get("hist_summary", {}),
        )
        L.extend(iv_lines)

    # ── Signal Score ──────────────────────────────────────────────
    bar = "[" + "#" * int(score / 5) + "." * (20 - int(score / 5)) + "]"
    votes_tag = "UNANIMOUS" if unanimous else "2/3 MAJORITY"
    if state.daily_trades_taken >= MAX_TRADES_PER_DAY:
        filter_line = f"  ** DAILY CAP REACHED ({MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY}) **"
    elif taken:
        filter_line = (f"  >> TRADE TAKEN  ({state.daily_trades_taken}/{MAX_TRADES_PER_DAY} today)"
                       f"  Score {score}/100 passed all gates")
    else:
        filter_line = (f"  -- SIGNAL SKIPPED  ({state.daily_trades_taken}/{MAX_TRADES_PER_DAY} taken)"
                       f"  Reason: {skip_reason}")

    a(f"  SIGNAL SCORE: {score:>3}/100  {bar}  Bias votes: {votes_tag}")
    if score_breakdown:
        bd = score_breakdown
        a(f"  Breakdown → Unanimity:{bd.get('unanimity',0):>2}  "
          f"PCR:{bd.get('pcr',0):>2}  "
          f"OI:{bd.get('oi_score',0):>2}  "
          f"MaxPain:{bd.get('max_pain',0):>2}  "
          f"VIX:{bd.get('vix',0):>2}  "
          f"Time:{bd.get('time',0):>2}  "
          f"RoC:{bd.get('roc',0):>2}  "
          f"RSI+VWAP:{bd.get('rsi_vwap',0):>2}")
    a(filter_line)
    a(THN)

    # ── Top OI additions ──────────────────────────────────────────
    top_ce = df.nlargest(3, "CE_Chg")[["Strike","CE_Chg","CE_LTP"]]
    top_pe = df.nlargest(3, "PE_Chg")[["Strike","PE_Chg","PE_LTP"]]
    a(f"  {'CALL OI ADDITIONS (overhead resistance)':<40}  PUT OI ADDITIONS (floor support)")
    for i in range(3):
        cr, pr = top_ce.iloc[i], top_pe.iloc[i]
        a(f"  {int(cr['Strike']):>7,} CE  +{int(cr['CE_Chg']):>9,}  Rs{cr['CE_LTP']:>6.1f}"
          f"    |    "
          f"{int(pr['Strike']):>7,} PE  +{int(pr['PE_Chg']):>9,}  Rs{pr['PE_LTP']:>6.1f}")
    a(THN)

    # ── Trade recommendations ─────────────────────────────────────
    from state import pcr_bullish, pcr_bearish
    a(f"  TRADE RECOMMENDATIONS  (bias={bias}, PCR bands: {pcr_bullish}/{pcr_bearish})")
    a(f"  {'#':<3} {'Strategy':<24} {'Strike':>7}  "
      f"{'Type':<5} {'Prem':>7}  {'SL':>7}  {'Target':>8}  {'1-Lot':>9}")
    a("  " + "." * (W - 2))
    for i, r in enumerate(recs, 1):
        a(f"  {i}   {r.label:<24} {r.strike:>7,}  "
          f"{r.opt_type:<5} Rs{r.premium:>5.1f}  Rs{r.sl:>5.1f}  "
          f"Rs{r.target:>6.1f}  Rs{r.lot_cost:>7,.0f}")
        a(f"      -> {r.reason}   R:R {r.rr}")
    a(THN)

    # ── RoC alerts ────────────────────────────────────────────────
    if roc_alerts:
        a("  ** RATE-OF-CHANGE ALERTS **")
        for al in roc_alerts:
            a(f"  >> {al}")
        a(THN)

    # ── Context ───────────────────────────────────────────────────
    a("  CONTEXT")
    a("  Price+ OI+  Long Buildup (bullish)  |  Price- OI+  Short Buildup (bearish)")
    a("  Price+ OI-  Short Covering (weak)   |  Price- OI-  Long Unwinding (temp dip)")
    try:
        vf = float(vix)
        a(f"  VIX {vix}: {'HIGH - widen SL, reduce qty' if vf > 20 else 'NORMAL - standard sizing'}")
    except (ValueError, TypeError):
        pass
    if demo:
        a(f"  [DEMO] Next market open: {next_open_str()}")
    a(f"  Signals logged: {len(state.signal_log)}  |  Log: {getattr(__import__('config'), 'LOG_FILE', 'OI.csv')}")
    a(SEP)

    # ── Chart ─────────────────────────────────────────────────────
    atm_df = df[(df["Strike"] >= spot * 0.96) & (df["Strike"] <= spot * 1.04)].copy()
    if atm_df.empty:
        atm_df = df.copy()

    st  = atm_df["Strike"].values
    bw  = max(10, (st.max() - st.min()) / max(len(st), 1) * 0.38)
    off = bw / 2
    atm = nearest_strike(spot, symbol)
    atm_idx_arr = [i for i, s in enumerate(st) if s == atm]
    atm_i = atm_idx_arr[0] if atm_idx_arr else None

    wt_dir, wt_clr = (("Bearish (CE buildup)", "#e74c3c") if net_score > 0 else
                      ("Bullish (PE buildup)", "#2ecc71") if net_score < 0 else
                      ("Neutral", "#f39c12"))

    # Determine how many subplots (add IV skew chart if data available)
    has_iv = (iv_data and iv_data.get("skew", {}).get("atm_iv") is not None
              and "CE_IV" in df.columns)
    ncols  = 3 if has_iv else 2

    fig, axes = plt.subplots(1, ncols, figsize=(5 * ncols + 3, 5))
    ax1, ax2  = axes[0], axes[1]
    ax3       = axes[2] if has_iv else None

    fig.patch.set_facecolor("#0d1117")
    for ax in axes:
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="white")
        for lbl in (ax.xaxis.label, ax.yaxis.label, ax.title):
            lbl.set_color("white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")

    atm_iv_str = (f"ATM IV {iv_data['skew']['atm_iv']:.2f}%  "
                  f"IVR {iv_data['hist_summary'].get('ivr','N/A')}"
                  if (iv_data and iv_data.get("skew", {}).get("atm_iv")) else "")

    fig.suptitle(
        f"{symbol} [{mode_tag}]  {now_ist().strftime('%d %b %Y %H:%M IST')}"
        f"  Spot Rs{spot:,.0f}  VIX {vix}  MaxPain Rs{int(max_pain):,}"
        f"  PCR(local) {local_pcr_str} → {pcr_signal}"
        + (f"  {atm_iv_str}" if atm_iv_str else ""),
        color="white", fontsize=10, fontweight="bold"
    )

    # ── Left: OI profile ─────────────────────────────────────────
    ce_bars = ax1.bar(st - off, atm_df["CE_OI"], width=bw,
                      color="#e74c3c", alpha=0.85, label="Call OI")
    pe_bars = ax1.bar(st + off, atm_df["PE_OI"], width=bw,
                      color="#2ecc71", alpha=0.85, label="Put OI")
    if atm_i is not None:
        for bar_set in (ce_bars, pe_bars):
            bar_set[atm_i].set_edgecolor("white")
            bar_set[atm_i].set_linewidth(2.5)

    max_oi_val = max(atm_df["CE_OI"].max(), atm_df["PE_OI"].max(), 1)
    for idx, (cbar, pbar) in enumerate(zip(ce_bars, pe_bars)):
        ce_chg = atm_df["CE_Chg"].iloc[idx]
        pe_chg = atm_df["PE_Chg"].iloc[idx]
        ce_vol = atm_df["CE_Vol"].iloc[idx] if "CE_Vol" in atm_df.columns else 0
        ax1.text(cbar.get_x() + cbar.get_width(),
                 cbar.get_height() + max_oi_val * 0.02,
                 f"Δ:{ce_chg/1000:+.0f}k", ha="center", va="bottom",
                 fontsize=6.5, color="#2ecc71" if ce_chg >= 0 else "#e74c3c", rotation=90)
        ax1.text(pbar.get_x() + pbar.get_width(),
                 pbar.get_height() + max_oi_val * 0.02,
                 f"Δ:{pe_chg/1000:+.0f}k", ha="center", va="bottom",
                 fontsize=6.5, color="#2ecc71" if pe_chg >= 0 else "#e74c3c", rotation=90)
        if ce_vol > 0:
            ax1.text(cbar.get_x() + cbar.get_width() / 2,
                     cbar.get_height() + max_oi_val * 0.08,
                     f"V:{ce_vol/1000:.0f}k", ha="center", va="bottom",
                     fontsize=5.5, color="#9b59b6", rotation=90)

    ax1.axvline(spot,       color="#3498db", lw=2,   ls="--", label="Spot")
    ax1.axvline(max_pain,   color="#f39c12", lw=1.5, ls=":",  label="MaxPain")
    ax1.axvline(resistance, color="#e74c3c", lw=1,   ls=":",  label="Res(ΔCE)")
    ax1.axvline(support,    color="#2ecc71", lw=1,   ls=":",  label="Sup(ΔPE)")
    ax1.text(0.5, 0.94, f"Wtd Direction: {wt_dir}",
             transform=ax1.transAxes, ha="center", va="top",
             fontsize=10, color=wt_clr, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.3", fc="#161b22", alpha=0.8))
    ax1.set_title("Open Interest  (white border=ATM)", color="white")
    ax1.set_xlabel("Strike", color="white"); ax1.set_ylabel("OI", color="white")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1e5:.1f}L"))
    ax1.legend(fontsize=7, facecolor="#161b22", labelcolor="white")
    ax1.grid(True, alpha=0.15, color="white")

    # ── Middle: OI change ─────────────────────────────────────────
    cc = ["#e74c3c" if v > 0 else "#555" for v in atm_df["CE_Chg"]]
    pc = ["#2ecc71" if v > 0 else "#555" for v in atm_df["PE_Chg"]]
    ax2.bar(st - off, atm_df["CE_Chg"], width=bw, color=cc, alpha=0.85, label="Call ΔOI")
    ax2.bar(st + off, atm_df["PE_Chg"], width=bw, color=pc, alpha=0.85, label="Put ΔOI")
    ax2.axhline(0,    color="white", lw=0.6)
    ax2.axvline(spot, color="#3498db", lw=2, ls="--")
    for r in recs:
        ax2.axvline(r.strike, color="#f39c12", lw=1.2, ls="-.", alpha=0.8)
    local_pcr_disp = f"{local_pcr:.3f}" if local_pcr is not None else "N/A"
    ax2.text(0.02, 0.97, f"Local PCR: {local_pcr_disp}\nSIGNAL: {pcr_signal}",
             transform=ax2.transAxes, fontsize=9, fontweight="bold",
             verticalalignment="top", color="white",
             bbox=dict(facecolor=pcr_signal_color, alpha=0.75, boxstyle="round,pad=0.4"))
    ax2.set_title("OI Change / Fresh Positions", color="white")
    ax2.set_xlabel("Strike", color="white"); ax2.set_ylabel("ΔOI", color="white")
    ax2.legend(fontsize=7, facecolor="#161b22", labelcolor="white")
    ax2.grid(True, alpha=0.15, color="white")

    # ── Right: IV Skew curve (v5.3 NEW) ──────────────────────────
    if ax3 is not None and "CE_IV" in df.columns:
        strikes_all = df["Strike"].values
        ce_iv_all   = df["CE_IV"].values
        pe_iv_all   = df["PE_IV"].values

        # Plot CE IV (call skew — right side)
        ax3.plot(strikes_all, ce_iv_all, color="#e74c3c", lw=2,
                 marker="o", markersize=3, label="Call IV")
        # Plot PE IV (put skew — left side)
        ax3.plot(strikes_all, pe_iv_all, color="#2ecc71", lw=2,
                 marker="o", markersize=3, label="Put IV")

        # Mark ATM min
        if atm is not None:
            atm_row = df[df["Strike"] == float(atm)]
            if not atm_row.empty:
                atm_iv_val = (atm_row["CE_IV"].iloc[0] + atm_row["PE_IV"].iloc[0]) / 2
                ax3.scatter([atm], [atm_iv_val], color="white", s=60, zorder=5,
                            label=f"ATM IV {atm_iv_val:.2f}%")
                ax3.axvline(atm, color="white", lw=1.2, ls="--", alpha=0.5)

        # Skew annotation box
        if iv_data:
            skew  = iv_data.get("skew", {})
            stext = (f"IV Skew: {skew.get('skew_pct', 0):+.2f}%\n"
                     f"{skew.get('direction','N/A')}\n"
                     f"OTM Put IV: {skew.get('otm_put_iv','N/A')}\n"
                     f"OTM Call IV: {skew.get('otm_call_iv','N/A')}")
            ax3.text(0.02, 0.97, stext,
                     transform=ax3.transAxes, fontsize=8,
                     verticalalignment="top", color="white",
                     bbox=dict(facecolor=skew.get("color","#8b949e"),
                               alpha=0.75, boxstyle="round,pad=0.4"))

        ax3.set_title("IV Skew Curve  (v5.3)", color="white")
        ax3.set_xlabel("Strike", color="white")
        ax3.set_ylabel("Implied Volatility (%)", color="white")
        ax3.legend(fontsize=7, facecolor="#161b22", labelcolor="white")
        ax3.grid(True, alpha=0.15, color="white")

    plt.tight_layout()

    # ── Atomic output ─────────────────────────────────────────────
    if HAS_IPYTHON:
        clear_output(wait=True)
        print("\n".join(L))
        ipy_display(fig)
    else:
        os.system("cls" if os.name == "nt" else "clear")
        print("\n".join(L))
        if SAVE_PLOT:
            plt.savefig(PLOT_FILE, dpi=110, bbox_inches="tight",
                        facecolor=fig.get_facecolor())

    plt.close(fig)
