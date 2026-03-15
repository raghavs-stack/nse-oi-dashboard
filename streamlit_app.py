"""
NSE OI Dashboard  v5.7  — Dynamic Streamlit Frontend
============================================================
New in v5.7:
  • Live delta indicators (change vs previous cycle)
  • Weekly + Monthly PCR on PCR chart
  • 20-EMA + VWAP overlays on PCR chart
  • Strategy Classifier panel (BUY / SELL / SPREAD)
  • Day Quality gauge (day-of-week + time window + expiry DTE)
  • PCR divergence signal (Weekly vs Monthly)
  • Countdown timer to next refresh

Run:
    python main.py --symbol NIFTY          # Terminal 1
    python main.py --symbol BANKNIFTY      # Terminal 2 (optional)
    streamlit run streamlit_app.py         # Terminal 3
"""

import json, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Page config ───────────────────────────────────────────────────
st.set_page_config(
    page_title="NSE OI Dashboard v5.7",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .main { background-color: #0d1117; }
    .block-container { padding-top: 1rem; }
    .stMetric { background: #161b22; border-radius: 8px; padding: 8px; }
    .signal-box { padding: 10px 16px; border-radius: 8px; font-weight: bold;
                  font-size: 17px; text-align: center; }
    .bullish  { background:#0f3d20; color:#2ecc71; border:1px solid #2ecc71; }
    .bearish  { background:#3d0f0f; color:#e74c3c; border:1px solid #e74c3c; }
    .neutral  { background:#3d3d0f; color:#f39c12; border:1px solid #f39c12; }
    .taken-yes{ background:#0a3d0a; color:#00ff88; }
    .taken-no { background:#1a1a1a; color:#888; }
    .strategy-buy  { background:#0d2137; color:#3498db; border:1px solid #3498db;
                     padding:10px; border-radius:8px; }
    .strategy-sell { background:#2d1010; color:#e74c3c; border:1px solid #e74c3c;
                     padding:10px; border-radius:8px; }
    .strategy-spread{ background:#1a1a2e; color:#9b59b6; border:1px solid #9b59b6;
                      padding:10px; border-radius:8px; }
    .day-good  { background:#0f3d20; color:#2ecc71; border-radius:6px; padding:8px; }
    .day-warn  { background:#3d2f0f; color:#f39c12; border-radius:6px; padding:8px; }
    .day-bad   { background:#3d0f0f; color:#e74c3c; border-radius:6px; padding:8px; }
    .countdown { font-size:12px; color:#555; text-align:right; }
    div[data-testid="stMetricValue"] { font-size:1.3rem !important; }
    div[data-testid="stMetricDelta"] { font-size:0.8rem !important; }
</style>
""", unsafe_allow_html=True)

REFRESH_S = 35
SYMBOLS   = ["NIFTY", "BANKNIFTY"]


# ── Helpers ───────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_S)
def load_log(path: str) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def load_state(symbol: str) -> dict:
    for p in [f"dashboard_state_{symbol}.json", "dashboard_state.json"]:
        if Path(p).exists():
            try:
                return json.loads(Path(p).read_text())
            except Exception:
                pass
    return {}


def _delta_str(cur, prev, fmt=".2f", invert=False):
    """Return (value_str, delta_str) for st.metric."""
    if cur is None:
        return "N/A", None
    val_str = f"{cur:{fmt}}" if isinstance(cur, float) else str(cur)
    if prev is None or prev == cur:
        return val_str, None
    diff = float(cur) - float(prev)
    arrow = "▲" if diff > 0 else "▼"
    delta = f"{arrow} {abs(diff):{fmt}}"
    return val_str, delta


def _gauge_html(score: int, label: str, color: str) -> str:
    pct = min(100, max(0, score))
    return f"""
    <div style="background:#161b22;border-radius:8px;padding:10px;text-align:center;">
      <div style="font-size:24px;font-weight:bold;color:{color};">{score}</div>
      <div style="font-size:11px;color:#888;margin:2px 0;">{label}</div>
      <div style="background:#21262d;border-radius:4px;height:6px;margin-top:6px;">
        <div style="background:{color};width:{pct}%;height:6px;border-radius:4px;"></div>
      </div>
    </div>"""


# ── IV Skew chart ─────────────────────────────────────────────────
# ── 3-panel OI + OI Change + IV Skew chart ─────────────────────────────────
def _three_panel_charts(state: dict):
    """
    Replicates the terminal.py matplotlib 3-panel chart inside Streamlit
    using Plotly.  Panels:
      Left   — OI Profile (ATM white border, Δ annotations, spot/max_pain/res/sup lines)
      Middle — OI Change / Fresh Positions (PCR box overlay)
      Right  — IV Skew Curve (ATM dot, skew annotation box)
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        st.warning("plotly not installed — run `pip install plotly`")
        return

    oi  = state.get("oi_chart", {})
    iv  = state.get("iv_chart", {})
    sym = state.get("symbol", "NIFTY")

    if not oi:
        st.info("⏳ Waiting for first data cycle…")
        return

    strikes   = oi.get("strikes",  [])
    ce_oi     = oi.get("ce_oi",    [])
    pe_oi     = oi.get("pe_oi",    [])
    ce_chg    = oi.get("ce_chg",   [0]*len(strikes))
    pe_chg    = oi.get("pe_chg",   [0]*len(strikes))
    ce_vol    = oi.get("ce_vol",   [0]*len(strikes))
    atm       = oi.get("atm",       0)
    max_pain  = oi.get("max_pain",  0)
    resistance= oi.get("resistance",0)
    support_l = oi.get("support",   0)
    wtd_dir   = oi.get("wtd_dir",   "Neutral")
    wtd_clr   = oi.get("wtd_clr",   "#f39c12")
    local_pcr = oi.get("local_pcr")
    pcr_sig   = oi.get("pcr_signal",  "N/A")
    pcr_clr   = oi.get("pcr_signal_color", "#f39c12")

    has_iv = bool(iv and iv.get("strikes") and
                  any(v > 0 for v in iv.get("ce_iv", []) + iv.get("pe_iv", [])))
    ncols  = 3 if has_iv else 2
    col_titles = ["Open Interest  (white border=ATM)",
                  "OI Change / Fresh Positions",
                  "IV Skew Curve  (v5.3)"][:ncols]

    # Wider OI panel, slightly narrower ΔOI and IV panels
    _col_w = [0.42, 0.30, 0.28][:ncols] if ncols == 3 else [0.55, 0.45]
    fig = make_subplots(rows=1, cols=ncols,
                        subplot_titles=col_titles,
                        column_widths=_col_w,
                        horizontal_spacing=0.05)

    # ── Helper: format OI axis labels (1L = 100k) ─────────────────
    def fmt_L(val):
        if val >= 1e7:  return f"{val/1e7:.1f}Cr"
        if val >= 1e5:  return f"{val/1e5:.1f}L"
        if val >= 1e3:  return f"{val/1e3:.0f}k"
        return str(int(val))

    # ── Panel 1: OI Profile ───────────────────────────────────────
    if strikes and ce_oi:
        max_oi = max(max(ce_oi, default=1), max(pe_oi, default=1), 1)

        # Call OI bars — white border on ATM bar
        ce_lc = ["white" if s == atm else "#e74c3c" for s in strikes]
        pe_lc = ["white" if s == atm else "#2ecc71" for s in strikes]
        ce_lw = [2.5 if s == atm else 0 for s in strikes]
        pe_lw = [2.5 if s == atm else 0 for s in strikes]

        fig.add_trace(go.Bar(
            x=strikes, y=ce_oi, name="Call OI",
            marker=dict(color="#e74c3c", opacity=0.85,
                        line=dict(color=ce_lc, width=ce_lw)),
            offsetgroup=0,
            text=[f"Δ:{v/1000:+.0f}k" for v in ce_chg],
            textposition="outside", textfont=dict(size=7, color="#aaa"),
            hovertemplate="Strike: %{x}<br>CE OI: %{y:,.0f}<br>ΔOI: %{text}",
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=strikes, y=pe_oi, name="Put OI",
            marker=dict(color="#2ecc71", opacity=0.85,
                        line=dict(color=pe_lc, width=pe_lw)),
            offsetgroup=1,
            text=[f"Δ:{v/1000:+.0f}k" for v in pe_chg],
            textposition="outside", textfont=dict(size=7, color="#aaa"),
            hovertemplate="Strike: %{x}<br>PE OI: %{y:,.0f}<br>ΔOI: %{text}",
        ), row=1, col=1)

        # Vertical reference lines
        for xval, lclr, lw, ldash, lname in [
            (state.get("spot",0),  "#3498db", 2,   "dash",  "Spot"),
            (max_pain,             "#f39c12", 1.5, "dot",   "MaxPain"),
            (resistance,           "#e74c3c", 1.0, "dot",   "Res(ΔCE)"),
            (support_l,            "#2ecc71", 1.0, "dot",   "Sup(ΔPE)"),
        ]:
            if xval and xval > 0:
                fig.add_vline(x=xval, line_color=lclr, line_width=lw,
                              line_dash=ldash, row=1, col=1,
                              annotation_text=lname,
                              annotation_position="top",
                              annotation_font=dict(size=8, color=lclr))

        # "Wtd Direction" annotation box on panel 1
        fig.add_annotation(
            text=f"<b>Wtd Direction: {wtd_dir}</b>",
            xref="x domain", yref="y domain",
            x=0.5, y=0.96,
            showarrow=False,
            font=dict(size=11, color=wtd_clr),
            bgcolor="#161b22", bordercolor=wtd_clr, borderwidth=1,
            borderpad=4, opacity=0.9,
            row=1, col=1,
        )

    # ── Panel 2: OI Change ────────────────────────────────────────
    if strikes and ce_chg:
        cc = ["#e74c3c" if v > 0 else ("#2ecc71" if v < 0 else "#555") for v in ce_chg]
        pc = ["#2ecc71" if v > 0 else ("#e74c3c" if v < 0 else "#555") for v in pe_chg]

        def _fmt_chg(v):
            if abs(v) >= 1e5:  return f"{v/1e5:+.1f}L"
            if abs(v) >= 1e3:  return f"{v/1e3:+.0f}k"
            return f"{int(v):+d}" if v != 0 else ""

        fig.add_trace(go.Bar(
            x=strikes, y=ce_chg, name="Call ΔOI",
            marker=dict(color=cc, opacity=0.85),
            offsetgroup=0,
            text=[_fmt_chg(v) for v in ce_chg],
            textposition="outside", textfont=dict(size=7, color="#aaa"),
            hovertemplate="Strike: %{x}<br>CE ΔOI: %{y:,.0f}",
        ), row=1, col=2)

        fig.add_trace(go.Bar(
            x=strikes, y=pe_chg, name="Put ΔOI",
            marker=dict(color=pc, opacity=0.85),
            offsetgroup=1,
            text=[_fmt_chg(v) for v in pe_chg],
            textposition="outside", textfont=dict(size=7, color="#aaa"),
            hovertemplate="Strike: %{x}<br>PE ΔOI: %{y:,.0f}",
        ), row=1, col=2)

        # Horizontal zero line + spot line
        fig.add_hline(y=0, line_color="white", line_width=0.6, row=1, col=2)
        if state.get("spot"):
            fig.add_vline(x=state["spot"], line_color="#3498db",
                          line_width=2, line_dash="dash", row=1, col=2)

        # PCR / Signal annotation box
        pcr_txt = f"{local_pcr:.3f}" if local_pcr else "N/A"
        fig.add_annotation(
            text=f"<b>Local PCR: {pcr_txt}<br>SIGNAL: {pcr_sig}</b>",
            xref="x2 domain", yref="y2 domain",
            x=0.04, y=0.97,
            showarrow=False,
            font=dict(size=10, color="white"),
            bgcolor=pcr_clr, bordercolor=pcr_clr, borderwidth=1,
            borderpad=5, opacity=0.85,
            align="left",
            row=1, col=2,
        )

    # ── Panel 3: IV Skew Curve ────────────────────────────────────
    if has_iv:
        iv_strikes = iv.get("strikes", [])
        ce_iv_v    = iv.get("ce_iv",   [])
        pe_iv_v    = iv.get("pe_iv",   [])
        iv_atm     = iv.get("atm",      0)

        fig.add_trace(go.Scatter(
            x=iv_strikes, y=ce_iv_v, name="Call IV",
            mode="lines+markers",
            line=dict(color="#e74c3c", width=2),
            marker=dict(size=4),
            hovertemplate="Strike: %{x}<br>Call IV: %{y:.2f}%",
        ), row=1, col=3)

        fig.add_trace(go.Scatter(
            x=iv_strikes, y=pe_iv_v, name="Put IV",
            mode="lines+markers",
            line=dict(color="#2ecc71", width=2),
            marker=dict(size=4),
            hovertemplate="Strike: %{x}<br>Put IV: %{y:.2f}%",
        ), row=1, col=3)

        # ATM white dot
        if iv_atm and iv_atm in iv_strikes:
            atm_idx  = iv_strikes.index(iv_atm)
            atm_iv_v = (ce_iv_v[atm_idx] + pe_iv_v[atm_idx]) / 2 if atm_idx < len(ce_iv_v) else 0
            if atm_iv_v > 0:
                fig.add_trace(go.Scatter(
                    x=[iv_atm], y=[atm_iv_v],
                    mode="markers",
                    marker=dict(color="white", size=10, line=dict(color="white", width=2)),
                    name=f"ATM IV {atm_iv_v:.2f}%",
                    showlegend=True,
                ), row=1, col=3)
                fig.add_vline(x=iv_atm, line_color="white", line_width=1.2,
                              line_dash="dash", opacity=0.5, row=1, col=3)

        # Skew annotation box
        skew_pct = iv.get("skew_pct")
        skew_dir = iv.get("skew_dir", "N/A")
        atm_iv_display = iv.get("atm_iv_val")
        # Derive OTM put/call IV from the extremes of the chain
        otm_put_iv = otm_call_iv = None
        if iv_strikes and ce_iv_v and pe_iv_v:
            otm_put_iv  = round(pe_iv_v[0],  2)  # lowest strike = deepest OTM put
            otm_call_iv = round(ce_iv_v[-1], 2)  # highest strike = deepest OTM call
        if skew_pct is not None:
            slines = [
                f"IV Skew: {skew_pct:+.2f}%",
                f"{skew_dir}",
            ]
            if otm_put_iv:  slines.append(f"OTM Put IV: {otm_put_iv}")
            if otm_call_iv: slines.append(f"OTM Call IV: {otm_call_iv}")
            fig.add_annotation(
                text="<br>".join(slines),
                xref="x3 domain", yref="y3 domain",
                x=0.04, y=0.97,
                showarrow=False,
                font=dict(size=9, color="white"),
                bgcolor="#8b6914", bordercolor="#f0a500",
                borderpad=5, borderwidth=1, opacity=0.85,
                align="left",
                row=1, col=3,
            )

    # ── Global layout ─────────────────────────────────────────────
    layout_kw = dict(
        barmode="group",
        plot_bgcolor="#161b22", paper_bgcolor="#0d1117",
        font=dict(color="#e6edf3", size=11),
        showlegend=True,
        legend=dict(bgcolor="#161b22", bordercolor="#30363d",
                    borderwidth=1, font=dict(size=9),
                    orientation="v", x=1.01, y=1),
        height=520,          # taller default; autosize fills fullscreen
        autosize=True,       # Plotly.js recalculates on container resize
        margin=dict(l=45, r=10, t=55, b=45),
    )
    # Per-axis dark styling
    for i in range(1, ncols + 1):
        layout_kw[f"xaxis{'' if i==1 else i}"] = dict(
            tickformat=",d", gridcolor="#21262d",
            tickfont=dict(size=9), title="Strike",
        )
        layout_kw[f"yaxis{'' if i==1 else i}"] = dict(
            gridcolor="#21262d",
            tickfont=dict(size=9),
        )
    # Format panel 1 y-axis in L notation
    layout_kw["yaxis"] = dict(
        gridcolor="#21262d", tickfont=dict(size=9), title="OI",
        tickformat=",.0s",
    )
    layout_kw["yaxis2"] = dict(
        gridcolor="#21262d", tickfont=dict(size=9), title="ΔOI",
    )
    if has_iv:
        layout_kw["yaxis3"] = dict(
            gridcolor="#21262d", tickfont=dict(size=9), title="IV (%)",
            rangemode="tozero",
        )
    fig.update_layout(**layout_kw)
    # Style subplot titles
    for ann in fig.layout.annotations:
        if ann.text in col_titles:
            ann.font = dict(color="white", size=13)

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"responsive": True, "displayModeBar": True,
                "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
    )


def _iv_chart(iv_state: dict):
    st.markdown("##### 📈 IV Skew Curve")
    if not iv_state or iv_state.get("no_data"):
        st.info("⏳ IV populates at 09:15 IST" if not iv_state else
                "⏳ IV = 0 — market closed.")
        return
    strikes, ce_iv, pe_iv, atm = (iv_state.get(k, []) for k in
                                   ["strikes","ce_iv","pe_iv","atm"])
    if not strikes or not any(v > 0 for v in ce_iv + pe_iv):
        st.info("⏳ IV = 0 — market closed.")
        return
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes, y=ce_iv, name="Call IV",
                                 line=dict(color="#e74c3c", width=2),
                                 mode="lines+markers", marker=dict(size=4)))
        fig.add_trace(go.Scatter(x=strikes, y=pe_iv, name="Put IV",
                                 line=dict(color="#2ecc71", width=2),
                                 mode="lines+markers", marker=dict(size=4)))
        if atm and atm in strikes:
            fig.add_vline(x=atm, line_dash="dash", line_color="#f39c12",
                          annotation_text=f"ATM {atm:,}", annotation_position="top right")
        fig.update_layout(
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            font=dict(color="#e6edf3"),
            xaxis=dict(title="Strike", gridcolor="#21262d", tickformat=",d"),
            yaxis=dict(title="IV (%)", gridcolor="#21262d", rangemode="tozero"),
            legend=dict(bgcolor="#161b22"),
            height=280, margin=dict(l=30, r=10, t=10, b=30),
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        df = pd.DataFrame({"Strike": strikes, "Call IV": ce_iv,
                           "Put IV": pe_iv}).set_index("Strike")
        st.line_chart(df, color=["#e74c3c", "#2ecc71"])


# ── PCR chart with Weekly, Monthly, EMA20, VWAP ──────────────────
def _pcr_chart(pcr_series: dict, df_log: pd.DataFrame):
    st.markdown("##### 📉 PCR — Weekly · Monthly · EMA20 · VWAP")
    has_series = pcr_series and pcr_series.get("times")

    if has_series:
        times   = pcr_series["times"]
        local   = pcr_series.get("local",   [])
        weekly  = pcr_series.get("weekly",  [])
        monthly = pcr_series.get("monthly", [])
        ema20   = pcr_series.get("ema20",   [])
        vwap    = pcr_series.get("vwap",    [])
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            if local:
                fig.add_trace(go.Scatter(x=times, y=local, name="PCR Local",
                    line=dict(color="#3498db", width=2), mode="lines"))
            if weekly and any(v != local[i] for i,v in enumerate(weekly) if i < len(local)):
                fig.add_trace(go.Scatter(x=times, y=weekly, name="PCR Weekly",
                    line=dict(color="#e74c3c", width=1, dash="dot"), mode="lines"))
            if monthly and any(v != local[i] for i,v in enumerate(monthly) if i < len(local)):
                fig.add_trace(go.Scatter(x=times, y=monthly, name="PCR Monthly",
                    line=dict(color="#9b59b6", width=1, dash="dash"), mode="lines"))
            if ema20:
                fig.add_trace(go.Scatter(x=times, y=ema20, name="EMA-20",
                    line=dict(color="#f39c12", width=2), mode="lines"))
            if vwap:
                fig.add_trace(go.Scatter(x=times, y=vwap, name="VWAP",
                    line=dict(color="#2ecc71", width=1, dash="longdash"), mode="lines"))
            # Neutral band
            fig.add_hrect(y0=0.9, y1=1.1, fillcolor="#f39c12", opacity=0.05,
                          annotation_text="Neutral zone", annotation_position="right")
            fig.update_layout(
                plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                font=dict(color="#e6edf3"),
                xaxis=dict(gridcolor="#21262d"),
                yaxis=dict(title="PCR", gridcolor="#21262d"),
                legend=dict(bgcolor="#161b22", orientation="h", y=1.1),
                height=280, margin=dict(l=30, r=10, t=30, b=30),
            )
            st.plotly_chart(fig, use_container_width=True)
            return
        except ImportError:
            pass

    # Fallback: st.line_chart from CSV
    if not df_log.empty and "PCR_Local" in df_log.columns and len(df_log) > 2:
        cols = {c: c.replace("PCR_","") for c in
                ["PCR_Local","PCR_Weekly","PCR_Monthly","PCR_EMA20","PCR_VWAP"]
                if c in df_log.columns}
        pcr_c = df_log[["Timestamp"] + list(cols.keys())].dropna(subset=["Timestamp"])
        pcr_c = pcr_c.set_index("Timestamp").rename(columns=cols)
        st.line_chart(pcr_c)
    else:
        st.info("PCR history builds up after a few cycles…")


# ── Strategy panel ────────────────────────────────────────────────
def _strategy_panel(sc: dict, day_qual: dict):
    if not sc:
        return
    stype = sc.get("strategy_type", "SPREAD")
    css   = {"BUY": "strategy-buy", "SELL": "strategy-sell"}.get(stype, "strategy-spread")
    icon  = {"BUY": "🟢", "SELL": "🔴", "SPREAD": "🟣"}.get(stype, "⚪")

    st.markdown("##### 🎯 Strategy Classifier")
    col1, col2 = st.columns([3, 2])
    with col1:
        st.markdown(
            f'<div class="{css}">'
            f'<b>{icon} {sc.get("strategy_name","—")}</b> '
            f'[{sc.get("iv_regime","—")}]<br>'
            f'<small>Entry: {sc.get("entry","—")}</small><br>'
            f'<small>Win: {sc.get("exit_win","—")}</small><br>'
            f'<small>Loss: {sc.get("exit_loss","—")} &nbsp;|&nbsp; DTE: {sc.get("ideal_dte","—")}</small>'
            f'</div>', unsafe_allow_html=True)
    with col2:
        conviction = sc.get("conviction", "LOW")
        lots = sc.get("sizing", {}).get("lots", 0)
        c_color = {"HIGH": "#2ecc71", "MEDIUM": "#f39c12", "LOW": "#e74c3c"}.get(conviction, "#888")
        st.markdown(
            f'<div style="background:#161b22;border-radius:8px;padding:10px;">'
            f'<div style="color:{c_color};font-weight:bold;font-size:18px;">{conviction}</div>'
            f'<div style="font-size:12px;color:#888;">Conviction</div>'
            f'<div style="font-size:20px;font-weight:bold;color:#e6edf3;margin-top:6px;">'
            f'{lots} Lot{"s" if lots != 1 else ""}</div>'
            f'<div style="font-size:11px;color:#666;">{sc.get("sizing",{}).get("note","")}</div>'
            f'</div>', unsafe_allow_html=True)
    if sc.get("note"):
        st.caption(f"💡 {sc['note']}")


# ── Day quality panel ─────────────────────────────────────────────
def _day_quality_panel(dq: dict):
    if not dq:
        return
    score   = dq.get("composite_score", 50)
    profile = dq.get("day_profile", {})
    tw      = dq.get("time_window",  {})
    exp     = dq.get("expiry_proximity", {})
    dtw     = dq.get("days_to_weekly",  "?")
    dtm     = dq.get("days_to_monthly", "?")

    color = ("#2ecc71" if score >= 65 else
             "#f39c12" if score >= 45 else "#e74c3c")
    day_css = ("day-good" if score >= 65 else
               "day-warn" if score >= 45 else "day-bad")
    trade_ok = dq.get("trade_today", False)

    st.markdown("##### 📅 Day Quality")
    d1, d2, d3, d4 = st.columns(4)
    d1.markdown(_gauge_html(score, "Day Score", color), unsafe_allow_html=True)
    d2.metric("Day", f"{dq.get('day_name','')} [{profile.get('label','')}]",
              delta="✅ TRADE" if trade_ok else "⏭ SKIP")
    d3.metric("Time Window", tw.get("label","—"),
              delta=f"Score {tw.get('score',0)}")
    d4.metric("Days to Expiry", f"W:{dtw}  M:{dtm}",
              delta=exp.get("strategy","—"))

    # Notes row
    nc1, nc2 = st.columns(2)
    if profile.get("note"):
        nc1.caption(f"📌 {profile['note']}")
    if exp.get("note"):
        nc2.caption(f"⏱ {exp['note']}")


# ── Full symbol render ────────────────────────────────────────────
def render_symbol(symbol: str):
    today    = datetime.now().strftime("%Y%m%d")
    log_path = f"{symbol}_OI_{today}.csv"
    df_log   = load_log(log_path)
    state    = load_state(symbol)

    if df_log.empty and not state:
        st.warning(
            f"⏳ No data for **{symbol}** yet.  \n"
            f"Start: `python main.py --symbol {symbol}`"
        )
        return

    # ── Previous cycle for deltas ─────────────────────────────────
    latest = df_log.iloc[-1] if not df_log.empty else {}
    prev   = df_log.iloc[-2] if len(df_log) >= 2 else {}

    def _get(key, default=None):
        v = latest.get(key, state.get(key, default))
        if isinstance(v, float) and not pd.notna(v):
            return default
        return v

    spot    = float(_get("Spot",      state.get("spot",   0)))
    prev_sp = float(prev.get("Spot",  spot)) if len(df_log) >= 2 else spot
    vix     = _get("VIX",             state.get("vix",    "N/A"))
    pcr     = float(_get("PCR_Local", state.get("pcr",    0)) or 0)
    prev_pcr= float(prev.get("PCR_Local", pcr)) if len(df_log) >= 2 else pcr
    score   = int(_get("Score",       state.get("score",  0)))
    prev_sc = int(prev.get("Score",   score)) if len(df_log) >= 2 else score
    bias    = str(_get("Bias",        state.get("bias",   "N/A")))
    atm_iv  = _get("ATM_IV",          state.get("atm_iv", None))
    ivr     = _get("IVR",             state.get("ivr",    None))
    ivp     = _get("IVP",             state.get("ivp",    None))
    ts      = state.get("ts", latest.get("Timestamp", "—") if not df_log.empty else "—")
    _ps     = state.get("pcr_series", {})
    # pcr_series stores scalars (from update()) — not lists
    _w_raw  = _ps.get("weekly");  _w_raw  = _w_raw[-1] if isinstance(_w_raw, list) else _w_raw
    _m_raw  = _ps.get("monthly"); _m_raw  = _m_raw[-1] if isinstance(_m_raw, list) else _m_raw
    w_pcr   = _get("PCR_Weekly",  _w_raw)
    m_pcr   = _get("PCR_Monthly", _m_raw)
    div_sig = _get("PCR_DivSignal",   "—")
    day_sc  = _get("DayScore",        state.get("day_quality", {}).get("composite_score", None))
    strat   = _get("Strategy",        state.get("strat_class", {}).get("strategy_name", None))
    conv    = _get("Conviction",      state.get("strat_class", {}).get("conviction", None))

    # ── KPI Row ───────────────────────────────────────────────────
    k1,k2,k3,k4,k5,k6,k7,k8 = st.columns(8)
    k1.metric("Spot",        f"₹{spot:,.1f}",
              delta=f"{spot-prev_sp:+.1f}" if prev_sp != spot else None)
    k2.metric("PCR Local",   f"{pcr:.3f}",
              delta=f"{pcr-prev_pcr:+.3f}" if prev_pcr != pcr else None)
    k3.metric("PCR Weekly",  f"{w_pcr:.3f}" if w_pcr else "—")
    k4.metric("PCR Monthly", f"{m_pcr:.3f}" if m_pcr else "—")
    k5.metric("VIX",         str(vix))
    k6.metric("Score",       f"{score}/100",
              delta=f"{score-prev_sc:+d}" if prev_sc != score else None)
    k7.metric("ATM IV",      f"{float(atm_iv):.1f}%" if atm_iv and atm_iv != "N/A" else "N/A")
    k8.metric("IVR/IVP",     f"{ivr:.0f}/{ivp:.0f}" if ivr and ivp else "—")

    # ── PCR Divergence banner ─────────────────────────────────────
    if div_sig and div_sig not in ("—", "ALIGNED", "None"):
        color = "#2ecc71" if div_sig == "BOUNCE LIKELY" else "#e74c3c"
        st.markdown(
            f'<div style="background:#1a1a2e;border-left:4px solid {color};'
            f'padding:8px 16px;border-radius:4px;margin:8px 0;">'
            f'⚡ PCR Divergence: <b style="color:{color}">{div_sig}</b> '
            f'(Weekly {"%0.3f" % w_pcr if w_pcr else "—"} vs Monthly {"%0.3f" % m_pcr if m_pcr else "—"})' 
            f'</div>', unsafe_allow_html=True)

    # ── Bias + Strategy row ───────────────────────────────────────
    st.markdown("---")
    bc1, bc2, bc3 = st.columns([2, 2, 3])

    bias_cls  = {"BULLISH":"bullish","BEARISH":"bearish"}.get(bias,"neutral")
    bias_icon = {"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"🟡"}.get(bias,"⚪")
    taken_now = str(latest.get("Taken","")).lower() == "true"
    skip_txt  = str(latest.get("SkipReason",""))

    with bc1:
        st.markdown(f'<div class="signal-box {bias_cls}">{bias_icon} {bias}</div>',
                    unsafe_allow_html=True)
        st.caption(f"Score {score}/100 | {'✅ TAKEN' if taken_now else skip_txt[:40]}")
    with bc2:
        if strat and conv:
            stype = state.get("strat_class",{}).get("strategy_type","SPREAD")
            sc_css = {"BUY":"strategy-buy","SELL":"strategy-sell"}.get(stype,"strategy-spread")
            sc_icon = {"BUY":"🟢","SELL":"🔴","SPREAD":"🟣"}.get(stype,"⚪")
            st.markdown(
                f'<div class="{sc_css}" style="padding:8px 12px;border-radius:8px;">'
                f'{sc_icon} <b>{strat}</b><br>'
                f'<small>Conviction: {conv}</small></div>',
                unsafe_allow_html=True)
    with bc3:
        if day_sc is not None:
            day_state = state.get("day_quality", {})
            profile   = day_state.get("day_profile", {})
            tw        = day_state.get("time_window",  {})
            day_color = ("#2ecc71" if int(day_sc) >= 65 else
                         "#f39c12" if int(day_sc) >= 45 else "#e74c3c")
            trade_ok  = day_state.get("trade_today", False)
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:8px 12px;'
                f'display:flex;align-items:center;gap:12px;">'
                f'<div style="text-align:center;">'
                f'<span style="font-size:22px;font-weight:bold;color:{day_color};">{int(day_sc)}</span>'
                f'<div style="font-size:10px;color:#888;">Day Score</div></div>'
                f'<div><b style="color:{day_color};">{"✅ TRADE" if trade_ok else "⏭ SKIP"}</b>'
                f'<div style="font-size:11px;color:#999;">{tw.get("label","")}'
                f' · {profile.get("label","")}</div>'
                f'<div style="font-size:11px;color:#777;">{day_state.get("best_strategy","")}</div></div>'
                f'</div>', unsafe_allow_html=True)

    # ── Advanced analytics ────────────────────────────────────────
    adv = state.get("adv", {})
    if adv:
        st.markdown("---")
        st.markdown("##### 🧠 Advanced Analytics")

        g  = adv.get("gamma",       {})
        sm = adv.get("smart_money", {})
        fl = adv.get("flow",        {})
        dh = adv.get("dealer",      {})
        bk = adv.get("breakout",    {})

        # ── Row 1: GEX + Dealer (numeric) ────────────────────────
        a1, a2 = st.columns(2)
        with a1:
            gex_color = "#2ecc71" if "Positive" in g.get("regime","") else "#e74c3c"
            flip      = g.get("flip_level", "?")
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:11px;color:#888;margin-bottom:4px;">⚡ GEX Regime</div>'
                f'<div style="font-size:17px;font-weight:bold;color:{gex_color};">'
                f'{g.get("regime","N/A")}</div>'
                f'<div style="font-size:12px;color:#aaa;margin-top:4px;">'
                f'Flip Level: <b>₹{flip:,}</b></div>'
                f'</div>', unsafe_allow_html=True)
        with a2:
            dh_color  = "#2ecc71" if dh.get("bias","") == "BULLISH" else "#e74c3c"
            pressure  = dh.get("pressure", 0)
            p_fmt     = f"{pressure/1e7:.1f}Cr" if abs(pressure) >= 1e7 else f"{pressure:+,}"
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:11px;color:#888;margin-bottom:4px;">🏦 Dealer Hedging</div>'
                f'<div style="font-size:17px;font-weight:bold;color:{dh_color};">'
                f'{dh.get("bias","N/A")}</div>'
                f'<div style="font-size:12px;color:#aaa;margin-top:4px;">'
                f'Pressure: <b>{p_fmt}</b></div>'
                f'</div>', unsafe_allow_html=True)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # ── Row 2: Smart Money + Options Flow (detailed strike tables) ──
        sm_col, fl_col = st.columns(2)

        with sm_col:
            dom = sm.get("dominant", "N/A")
            mode = sm.get("mode", "")
            dom_color = "#2ecc71" if dom == "CALLS" else ("#e74c3c" if dom == "PUTS" else "#888")
            c_add    = sm.get("call_oi_add", 0); p_add = sm.get("put_oi_add", 0)
            dlabel   = sm.get("display_label", "OI Adds")
            _sm_mode = sm.get("mode", "")
            sm_mode_labels = {"vol+oi":"vol×oi","oi-chg":"oi-chg","abs-oi":"abs OI","oi-only":"oi-only"}
            sm_mode_str = sm_mode_labels.get(_sm_mode, _sm_mode)
            mode_badge = (f' <span style="font-size:10px;color:#555;">({sm_mode_str})</span>'
                          if _sm_mode else "")
            def _sm_fmt(v):
                return (f"{v/1e6:.1f}M" if v >= 1e6 else f"{v/1e3:.0f}K" if v >= 1e3 else str(v))
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:11px;color:#888;margin-bottom:4px;">'
                f'💎 Smart Money{mode_badge}</div>'
                f'<div style="font-size:17px;font-weight:bold;color:{dom_color};">'
                f'{dom} DOMINANT</div>'
                f'<div style="font-size:11px;color:#aaa;margin-top:2px;">'
                f'{dlabel} — Call: <b>{_sm_fmt(c_add)}</b> &nbsp;|&nbsp; Put: <b>{_sm_fmt(p_add)}</b></div>'
                f'</div>', unsafe_allow_html=True)

            # Top strikes table
            calls_data  = sm.get("calls", [])
            puts_data   = sm.get("puts",  [])
            _sm_mode_c  = sm.get("mode", "")
            _sm_lbl     = "OI" if _sm_mode_c == "abs-oi" else "OI Δ"
            def _sval(v):
                return (f"{v/1e6:.1f}M" if abs(v) >= 1e6 else
                        f"{v/1e3:.0f}K" if abs(v) >= 1e3 else
                        (f"{v:,}" if _sm_mode_c == "abs-oi" else
                         (f"+{v:,}" if v >= 0 else f"{v:,}")))
            if calls_data or puts_data:
                t1, t2 = st.columns(2)
                with t1:
                    st.markdown("**📞 Top Call Strikes**")
                    for item in calls_data:
                        strike = item[0] if isinstance(item, (list,tuple)) else item
                        oi_val = item[1] if isinstance(item, (list,tuple)) and len(item) > 1 else 0
                        st.markdown(
                            f'<div style="background:#0f2d1a;border-radius:4px;padding:4px 8px;'
                            f'margin:2px 0;font-size:12px;">'
                            f'<b>₹{strike:,}</b> CE &nbsp; {_sm_lbl}: '
                            f'<span style="color:#2ecc71">{_sval(oi_val)}</span></div>',
                            unsafe_allow_html=True)
                with t2:
                    st.markdown("**📉 Top Put Strikes**")
                    for item in puts_data:
                        strike = item[0] if isinstance(item, (list,tuple)) else item
                        oi_val = item[1] if isinstance(item, (list,tuple)) and len(item) > 1 else 0
                        st.markdown(
                            f'<div style="background:#2d0f0f;border-radius:4px;padding:4px 8px;'
                            f'margin:2px 0;font-size:12px;">'
                            f'<b>₹{strike:,}</b> PE &nbsp; {_sm_lbl}: '
                            f'<span style="color:#e74c3c">{_sval(oi_val)}</span></div>',
                            unsafe_allow_html=True)

        with fl_col:
            fl_bias  = fl.get("bias", "N/A")
            fl_mode  = fl.get("mode", "")
            fl_color = ("#e74c3c" if fl_bias == "BEARISH" else
                        "#2ecc71" if fl_bias == "BULLISH" else
                        "#f39c12" if fl_bias == "NEUTRAL" else "#888")
            c_flow   = fl.get("call_flow", 0); p_flow = fl.get("put_flow", 0)
            fl_mode_labels = {"vol+oi":"vol×oi","oi-chg":"oi-chg","abs-oi":"abs OI","oi-only":"oi-only"}
            fl_mode_str    = fl_mode_labels.get(fl_mode, fl_mode)
            fl_metric_lbl  = "Total OI" if fl_mode == "abs-oi" else "Flow"
            mode_badge2 = (f' <span style="font-size:10px;color:#555;">({fl_mode_str})</span>'
                           if fl_mode else "")
            def _ffmt(v):
                return (f"{v/1e7:.1f}Cr" if abs(v) >= 1e7 else
                        f"{v/1e6:.1f}M"  if abs(v) >= 1e6 else
                        f"{v/1e3:.0f}K"  if abs(v) >= 1e3 else str(v))
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:11px;color:#888;margin-bottom:4px;">'
                f'🌊 Options Flow{mode_badge2}</div>'
                f'<div style="font-size:17px;font-weight:bold;color:{fl_color};">'
                f'{fl_bias}</div>'
                f'<div style="font-size:11px;color:#aaa;margin-top:2px;">'
                f'Call {fl_metric_lbl}: <b>{_ffmt(c_flow)}</b> &nbsp;|&nbsp; '
                f'Put {fl_metric_lbl}: <b>{_ffmt(p_flow)}</b></div>'
                f'</div>', unsafe_allow_html=True)

            # Top flow strikes
            top_calls_fl = fl.get("top_calls", [])
            top_puts_fl  = fl.get("top_puts",  [])
            if not top_calls_fl:
                top_calls_fl = [(s, 0, 0) for s in fl.get("call_strikes", [])]
            if not top_puts_fl:
                top_puts_fl  = [(s, 0, 0) for s in fl.get("put_strikes", [])]

            def _fval(v):
                return (f"{v/1e7:.1f}Cr" if abs(v) >= 1e7 else
                        f"{v/1e6:.1f}M"  if abs(v) >= 1e6 else
                        f"{v/1e3:.0f}K"  if abs(v) >= 1e3 else f"{v:,}")
            _fl_oi_lbl   = "OI"       if fl_mode == "abs-oi" else "OI Δ"
            _fl_flow_lbl = "Total OI" if fl_mode == "abs-oi" else "Flow"

            if top_calls_fl or top_puts_fl:
                f1, f2 = st.columns(2)
                with f1:
                    st.markdown("**📞 Call Flow**")
                    for item in top_calls_fl:
                        strike = item[0] if isinstance(item, (list,tuple)) else item
                        oi_val = item[1] if isinstance(item, (list,tuple)) and len(item) > 1 else 0
                        flow   = item[2] if isinstance(item, (list,tuple)) and len(item) > 2 else 0
                        oi_str = _fval(oi_val)
                        fl_str = _fval(flow)
                        st.markdown(
                            f'<div style="background:#0f2d1a;border-radius:4px;padding:4px 8px;'
                            f'margin:2px 0;font-size:12px;">'
                            f'<b>₹{strike:,}</b> CE &nbsp; '
                            f'{_fl_oi_lbl}: <span style="color:#2ecc71">{oi_str}</span> &nbsp; '
                            f'{_fl_flow_lbl}: {fl_str}</div>', unsafe_allow_html=True)
                with f2:
                    st.markdown("**📉 Put Flow**")
                    for item in top_puts_fl:
                        strike = item[0] if isinstance(item, (list,tuple)) else item
                        oi_val = item[1] if isinstance(item, (list,tuple)) and len(item) > 1 else 0
                        flow   = item[2] if isinstance(item, (list,tuple)) and len(item) > 2 else 0
                        oi_str = _fval(oi_val)
                        fl_str = _fval(flow)
                        st.markdown(
                            f'<div style="background:#2d0f0f;border-radius:4px;padding:4px 8px;'
                            f'margin:2px 0;font-size:12px;">'
                            f'<b>₹{strike:,}</b> PE &nbsp; '
                            f'{_fl_oi_lbl}: <span style="color:#e74c3c">{oi_str}</span> &nbsp; '
                            f'{_fl_flow_lbl}: {fl_str}</div>', unsafe_allow_html=True)

        with fl_col:
            fl_bias = fl.get("bias", "N/A")
            fl_mode = fl.get("mode", "")
            fl_color = ("#e74c3c" if fl_bias == "BEARISH" else
                        "#2ecc71" if fl_bias == "BULLISH" else "#888")
            c_flow = fl.get("call_flow", 0); p_flow = fl.get("put_flow", 0)
            net    = fl.get("net_flow", 0)
            c_fmt = f"{c_flow/1e6:.1f}M" if c_flow >= 1e6 else f"{c_flow:,}"
            p_fmt2= f"{p_flow/1e6:.1f}M" if p_flow >= 1e6 else f"{p_flow:,}"
            mode_badge2 = f' <span style="font-size:10px;color:#555;">({fl_mode})</span>' if fl_mode else ""
            st.markdown(
                f'<div style="background:#161b22;border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:11px;color:#888;margin-bottom:4px;">'
                f'🌊 Options Flow{mode_badge2}</div>'
                f'<div style="font-size:17px;font-weight:bold;color:{fl_color};">'
                f'{fl_bias}</div>'
                f'<div style="font-size:11px;color:#aaa;margin-top:2px;">'
                f'Calls: <b>{c_fmt}</b> &nbsp;|&nbsp; Puts: <b>{p_fmt2}</b></div>'
                f'</div>', unsafe_allow_html=True)

            # Top flow strikes
            top_calls_fl = fl.get("top_calls", [])
            top_puts_fl  = fl.get("top_puts",  [])
            if not top_calls_fl:
                top_calls_fl = [(s, 0, 0) for s in fl.get("call_strikes", [])]
            if not top_puts_fl:
                top_puts_fl  = [(s, 0, 0) for s in fl.get("put_strikes", [])]

            if top_calls_fl or top_puts_fl:
                f1, f2 = st.columns(2)
                with f1:
                    st.markdown("**📞 Call Flow**")
                    for item in top_calls_fl:
                        strike = item[0] if isinstance(item, (list,tuple)) else item
                        oi_chg = item[1] if isinstance(item, (list,tuple)) and len(item)>1 else 0
                        flow   = item[2] if isinstance(item, (list,tuple)) and len(item)>2 else 0
                        oi_str = f"+{oi_chg:,}" if oi_chg >= 0 else f"{oi_chg:,}"
                        flow_str = f"{flow/1e6:.1f}M" if abs(flow) >= 1e6 else f"{flow:,}"
                        st.markdown(
                            f'<div style="background:#0f2d1a;border-radius:4px;padding:4px 8px;'
                            f'margin:2px 0;font-size:12px;">'
                            f'<b>₹{strike:,}</b> CE &nbsp; '
                            f'OI: <span style="color:#2ecc71">{oi_str}</span> &nbsp; '
                            f'Flow: {flow_str}</div>', unsafe_allow_html=True)
                with f2:
                    st.markdown("**📉 Put Flow**")
                    for item in top_puts_fl:
                        strike = item[0] if isinstance(item, (list,tuple)) else item
                        oi_chg = item[1] if isinstance(item, (list,tuple)) and len(item)>1 else 0
                        flow   = item[2] if isinstance(item, (list,tuple)) and len(item)>2 else 0
                        oi_str = f"+{oi_chg:,}" if oi_chg >= 0 else f"{oi_chg:,}"
                        flow_str = f"{flow/1e6:.1f}M" if abs(flow) >= 1e6 else f"{flow:,}"
                        st.markdown(
                            f'<div style="background:#2d0f0f;border-radius:4px;padding:4px 8px;'
                            f'margin:2px 0;font-size:12px;">'
                            f'<b>₹{strike:,}</b> PE &nbsp; '
                            f'OI: <span style="color:#e74c3c">{oi_str}</span> &nbsp; '
                            f'Flow: {flow_str}</div>', unsafe_allow_html=True)

        if bk.get("breakout"):
            st.error(f"⚡ **{bk.get('signal')}** | Res ₹{bk.get('resistance','?'):,} | Sup ₹{bk.get('support','?'):,}")

    # ── Trade recs ────────────────────────────────────────────────
    # ── v5.10 Analytics: Regime + Greeks + Cone + Kelly ─────────────
    _regime    = state.get("regime",        {})
    _vcone     = state.get("vcone",          {})
    _kelly     = state.get("kelly",          {})
    _ranked    = state.get("ranked_strikes", [])

    # ── 1. Market Regime (Hurst exponent) ────────────────────────────
    if _regime:
        st.markdown("---")
        st.markdown("##### 📊 Market Regime")
        _rg   = _regime.get("regime",      "RANGING")
        _h    = _regime.get("hurst",        None)
        _ac   = _regime.get("autocorr",     None)
        _buy  = _regime.get("buy_options",  False)
        _rnote= _regime.get("regime_note",  "")
        _vinfo= _regime.get("vol_info",     {})
        _rcolor = ("#2ecc71" if _buy else
                   "#e74c3c" if _rg in ("MEAN_REVERTING","RANGING","PANIC") else "#f39c12")
        _ricon  = "✅" if _buy else "⚠️"
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.markdown(
            f'<div style="background:#161b22;border-radius:8px;padding:10px;text-align:center;">'
            f'<div style="font-size:10px;color:#888;">Regime</div>'
            f'<div style="font-size:16px;font-weight:bold;color:{_rcolor};">{_ricon} {_rg}</div>'
            f'</div>', unsafe_allow_html=True)
        rc2.markdown(
            f'<div style="background:#161b22;border-radius:8px;padding:10px;text-align:center;">'
            f'<div style="font-size:10px;color:#888;">Hurst H</div>'
            f'<div style="font-size:16px;font-weight:bold;color:#aaa;">'
            f'{"H=" + str(_h) if _h else "warming up"}</div>'
            f'<div style="font-size:10px;color:#666;">H>0.58=trend</div>'
            f'</div>', unsafe_allow_html=True)
        rc3.markdown(
            f'<div style="background:#161b22;border-radius:8px;padding:10px;text-align:center;">'
            f'<div style="font-size:10px;color:#888;">Autocorr</div>'
            f'<div style="font-size:16px;font-weight:bold;color:#aaa;">'
            f'{_ac:.3f if isinstance(_ac, float) else "N/A"}</div>'
            f'<div style="font-size:10px;color:#666;">+ve=momentum</div>'
            f'</div>', unsafe_allow_html=True)
        _volz = _vinfo.get("vol_z", 0)
        _vlbl = _vinfo.get("regime", "NORMAL")
        rc4.markdown(
            f'<div style="background:#161b22;border-radius:8px;padding:10px;text-align:center;">'
            f'<div style="font-size:10px;color:#888;">Vol Regime</div>'
            f'<div style="font-size:16px;font-weight:bold;color:#aaa;">{_vlbl}</div>'
            f'<div style="font-size:10px;color:#666;">z={_volz:.2f}</div>'
            f'</div>', unsafe_allow_html=True)
        st.caption(f"💡 {_rnote}")

    # ── 2. Volatility Cone ────────────────────────────────────────────
    if _vcone:
        st.markdown("---")
        st.markdown("##### 🌀 Volatility Cone — IV vs Realized Vol")
        _vc_sig   = _vcone.get("signal",      "FAIR")
        _vc_rv20  = _vcone.get("rv_20",        None)
        _vc_rv60  = _vcone.get("rv_60",        None)
        _vc_ratio = _vcone.get("iv_rv_ratio",  None)
        _vc_cpct  = _vcone.get("cone_pct",     None)
        _vc_edge  = _vcone.get("buy_edge",     False)
        _vc_color = ("#2ecc71" if _vc_sig == "CHEAP" else
                     "#e74c3c" if _vc_sig == "EXPENSIVE" else "#f39c12")
        vc1, vc2, vc3, vc4 = st.columns(4)
        vc1.metric("IV vs RV", _vc_sig, delta="BUY EDGE ✅" if _vc_edge else "No edge")
        vc2.metric("IV/RV20 Ratio",
                   f"{_vc_ratio:.2f}" if _vc_ratio else "N/A",
                   delta="cheap" if _vc_ratio and _vc_ratio < 1.0 else "expensive")
        vc3.metric("RV20", f"{_vc_rv20:.1f}%" if _vc_rv20 else "N/A")
        vc4.metric("Cone %ile", f"{_vc_cpct:.0f}%" if _vc_cpct else "N/A")

    # ── 3. Greeks Dashboard ───────────────────────────────────────────
    if _ranked:
        st.markdown("---")
        st.markdown("##### ⚗️ Options Greeks & EV — Ranked Strikes")
        _grade_colors = {"A": "#2ecc71", "B": "#3498db", "C": "#f39c12", "SKIP": "#e74c3c"}
        for _rs in _ranked:
            _gc  = _grade_colors.get(_rs.get("grade", "C"), "#aaa")
            _pct = _rs.get("ev_pct", 0)
            _evc = "#2ecc71" if _pct >= 20 else ("#f39c12" if _pct >= 10 else "#e74c3c")
            st.markdown(
                f'<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;'
                f'padding:10px 14px;margin-bottom:8px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-weight:bold;color:#e6edf3;">'
                f'₹{_rs.get("strike",0):,} {_rs.get("opt_type","")} — {_rs.get("label","")}</span>'
                f'<span style="background:{_gc};color:#000;border-radius:4px;padding:2px 8px;'
                f'font-weight:bold;font-size:12px;">Grade {_rs.get("grade","?")}</span></div>'
                f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-top:8px;">'
                f'<div style="text-align:center;"><div style="font-size:10px;color:#888;">Δ Delta</div>'
                f'<div style="font-size:14px;color:#aaa;">{_rs.get("delta",0):+.3f}</div></div>'
                f'<div style="text-align:center;"><div style="font-size:10px;color:#888;">Γ Gamma</div>'
                f'<div style="font-size:14px;color:#aaa;">{_rs.get("gamma",0):.5f}</div></div>'
                f'<div style="text-align:center;"><div style="font-size:10px;color:#888;">θ Theta/d</div>'
                f'<div style="font-size:14px;color:#e74c3c;">{_rs.get("theta",0):.2f}</div></div>'
                f'<div style="text-align:center;"><div style="font-size:10px;color:#888;">ν Vega/%</div>'
                f'<div style="font-size:14px;color:#3498db;">{_rs.get("vega",0):.2f}</div></div>'
                f'<div style="text-align:center;"><div style="font-size:10px;color:#888;">P(ITM)</div>'
                f'<div style="font-size:14px;color:#aaa;">{_rs.get("prob_itm",0):.0%}</div></div>'
                f'<div style="text-align:center;"><div style="font-size:10px;color:#888;">EV %</div>'
                f'<div style="font-size:14px;font-weight:bold;color:{_evc};">{_pct:+.0f}%</div></div>'
                f'</div></div>', unsafe_allow_html=True)

    # ── 4. Kelly Position Sizing ──────────────────────────────────────
    if _kelly:
        st.markdown("---")
        st.markdown("##### 🎲 Kelly Position Sizing")
        _kf   = _kelly.get("kelly_f",   0)
        _lots = _kelly.get("lots",       1)
        _wr   = _kelly.get("win_rate",  0.5)
        _conf = _kelly.get("confidence","DEFAULT")
        _ntrd = _kelly.get("n_trades",   0)
        _knote= _kelly.get("note",       "")
        _kcolor = "#2ecc71" if _lots >= 2 else ("#f39c12" if _lots == 1 else "#e74c3c")
        ka, kb, kc, kd = st.columns(4)
        ka.metric("Lots",        f"{_lots} lot(s)",
                  delta=f"Kelly f*={_kf:.1%}")
        kb.metric("Win Rate",    f"{_wr:.0%}",
                  delta=f"{_ntrd} trades")
        kc.metric("Confidence",  _conf)
        kd.markdown(
            f'<div style="background:#161b22;border-radius:8px;padding:8px 12px;">'
            f'<div style="font-size:10px;color:#888;">Sizing note</div>'
            f'<div style="font-size:11px;color:#aaa;margin-top:3px;">{_knote}</div>'
            f'</div>', unsafe_allow_html=True)

    # ── ML Ensemble Panel ────────────────────────────────────────
    ml_res = state.get("ml_result", {})
    if ml_res:
        st.markdown("---")
        st.markdown("##### 🤖 ML Ensemble Signal")
        _ml_sig  = ml_res.get("signal",          "NEUTRAL")
        _ml_con  = ml_res.get("confidence",      0.5)
        _ml_pts  = ml_res.get("score_pts",       0)
        _ml_rows = ml_res.get("data_rows",       0)
        _ml_syn  = ml_res.get("using_synthetic", True)
        _ml_trn  = ml_res.get("trained",         False)
        _ml_mdl  = ml_res.get("models",          {})
        _sig_color = ("green" if _ml_sig == "CONFIRM" else
                      "red"   if _ml_sig == "CONTRA"  else "orange")
        _sig_icon  = ("✅" if _ml_sig == "CONFIRM" else
                      "❌" if _ml_sig == "CONTRA"  else "⬜")
        _src_lbl   = ("synthetic" if _ml_syn else f"{_ml_rows:,} real cycles")

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("ML Signal",   f"{_sig_icon} {_ml_sig}")
        mc2.metric("Confidence",  f"{_ml_con:.0%}")
        mc3.metric("Score Δ",     f"{_ml_pts:+d} pts")
        mc4.metric("Trained on",  _src_lbl)

        if _ml_trn and _ml_mdl:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            b1, b2, b3, b4 = st.columns(4)
            for _bcol, _blbl, _bkey in [
                (b1, "🌲 RandomForest", "rf"),
                (b2, "📈 LogisticReg",  "lr"),
                (b3, "⚡ XGBoost/GBT",  "xgb"),
                (b4, "🧠 Gluon/MLP",    "mlp"),
            ]:
                _bv = _ml_mdl.get(_bkey, 0.5)
                _bc = ("#2ecc71" if _bv >= 0.65 else
                       "#e74c3c" if _bv <= 0.38 else "#f39c12")
                _bcol.markdown(
                    f'<div style="background:#161b22;border-radius:6px;'
                    f'padding:8px 10px;text-align:center;">'
                    f'<div style="font-size:10px;color:#888;">{_blbl}</div>'
                    f'<div style="font-size:20px;font-weight:bold;'
                    f'color:{_bc};">{_bv:.0%}</div></div>',
                    unsafe_allow_html=True)

    recs = state.get("recs", [])
    if recs:
        st.markdown("---")
        st.markdown("##### 🎯 Trade Recommendations")
        for col, r in zip(st.columns(len(recs)), recs):
            with col:
                st.markdown(f"**{r.get('label','Rec')}**")
                st.metric(f"{r.get('strike',0):,} {r.get('opt_type','')}",
                          f"₹{r.get('premium',0):.1f}")
                st.caption(f"SL: ₹{r.get('sl',0):.0f}  Target: ₹{r.get('target',0):.0f}")
                st.caption(r.get("reason",""))

    # ── Charts: 3-panel (OI Profile | OI Change | IV Skew) ───────
    st.markdown("---")
    _three_panel_charts(state)

    # ── PCR chart with EMA/VWAP/Weekly/Monthly ────────────────────
    st.markdown("---")
    _pcr_chart(state.get("pcr_series", {}), df_log)

    # ── Strategy + Day quality detail ─────────────────────────────
    st.markdown("---")
    sq1, sq2 = st.columns([3, 2])
    with sq1:
        _strategy_panel(state.get("strat_class", {}), state.get("day_quality", {}))
    with sq2:
        _day_quality_panel(state.get("day_quality", {}))

    # ── Signal history ────────────────────────────────────────────
    if not df_log.empty:
        st.markdown("---")
        st.markdown("##### 📋 Signal History (today)")
        base_cols = ["Timestamp","Spot","Bias","Score","PCR_Local",
                     "PCR_Weekly","PCR_Monthly","ATM_IV","IVR","IVP",
                     "Strategy","Conviction","DayScore","TimeWindow",
                     "Taken","SkipReason","Rec1","Rec2","Rec3"]
        show_cols = [c for c in base_cols if c in df_log.columns]
        st.dataframe(df_log[show_cols].tail(20).iloc[::-1],
                     use_container_width=True, hide_index=True)

        # Score history
        if "Score" in df_log.columns and len(df_log) > 2:
            sc_df = df_log[["Timestamp","Score"]].dropna()
            sc_df = sc_df.set_index("Timestamp")
            try:
                import plotly.graph_objects as go
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=sc_df.index, y=sc_df["Score"],
                    mode="lines+markers", line=dict(color="#f39c12", width=2),
                    name="Score"))
                fig.add_hline(y=55, line_dash="dash", line_color="#2ecc71",
                              annotation_text="Min threshold 55")
                fig.add_hline(y=70, line_dash="dot",  line_color="#e74c3c",
                              annotation_text="Strong signal 70")
                fig.update_layout(
                    plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                    font=dict(color="#e6edf3"),
                    xaxis=dict(gridcolor="#21262d"),
                    yaxis=dict(title="Score", gridcolor="#21262d", range=[0,100]),
                    height=200, margin=dict(l=30,r=10,t=10,b=30),
                )
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                st.line_chart(sc_df, color=["#f39c12"])

    st.caption(f"Symbol: {symbol}  |  Log: `{log_path}`  |  v5.7 Shoonya")


# ════════════════════════════════════════════════════════════════
#  Page layout
# ════════════════════════════════════════════════════════════════
hdr1, hdr2, hdr3 = st.columns([5, 1, 1])
with hdr1:
    st.markdown("## 📊 NSE OI Dashboard  `v5.7`")
with hdr2:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()
with hdr3:
    remaining = REFRESH_S - (int(time.time()) % REFRESH_S)
    st.markdown(f'<div class="countdown">Next refresh<br>⏱ {remaining}s</div>',
                unsafe_allow_html=True)

st.caption(
    f"Auto-refreshes every {REFRESH_S}s  •  Shoonya API  •  "
    f"{datetime.now().strftime('%d %b %Y  %H:%M:%S IST')}"
)

nf_up  = Path("dashboard_state_NIFTY.json").exists()
bnf_up = Path("dashboard_state_BANKNIFTY.json").exists()
legacy = Path("dashboard_state.json").exists()

if not nf_up and not bnf_up and not legacy:
    st.warning(
        "⏳ No data yet.  Start an engine:\n\n"
        "```\npython main.py --symbol NIFTY\n"
        "python main.py --symbol BANKNIFTY\n```"
    )
    time.sleep(REFRESH_S)
    st.rerun()

if nf_up and bnf_up:
    tab_nf, tab_bnf = st.tabs(["🔵 NIFTY", "🟠 BANKNIFTY"])
    with tab_nf:
        render_symbol("NIFTY")
    with tab_bnf:
        render_symbol("BANKNIFTY")
elif bnf_up:
    render_symbol("BANKNIFTY")
else:
    render_symbol("NIFTY")

st.markdown("---")
st.caption(f"Last rendered: {datetime.now().strftime('%H:%M:%S IST')}")
time.sleep(REFRESH_S)
st.rerun()
