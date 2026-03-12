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

    # ── Charts: OI + IV side by side ─────────────────────────────
    st.markdown("---")
    ch1, ch2 = st.columns(2)
    with ch1:
        st.markdown("##### 📊 Open Interest Profile")
        oi_state = state.get("oi_chart", {})
        if oi_state:
            df_oi = pd.DataFrame({
                "Strike":  oi_state.get("strikes",[]),
                "Call OI": oi_state.get("ce_oi",  []),
                "Put OI":  oi_state.get("pe_oi",  []),
            }).set_index("Strike")
            if not df_oi.empty:
                try:
                    import plotly.graph_objects as go
                    fig = go.Figure()
                    strikes = list(df_oi.index)
                    fig.add_trace(go.Bar(x=strikes, y=df_oi["Call OI"].tolist(),
                                         name="Call OI", marker_color="#e74c3c"))
                    fig.add_trace(go.Bar(x=strikes, y=df_oi["Put OI"].tolist(),
                                         name="Put OI",  marker_color="#2ecc71"))
                    fig.update_layout(
                        barmode="group", plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                        font=dict(color="#e6edf3"),
                        xaxis=dict(tickformat=",d", gridcolor="#21262d"),
                        yaxis=dict(gridcolor="#21262d"),
                        legend=dict(bgcolor="#161b22"),
                        height=280, margin=dict(l=30,r=10,t=10,b=30),
                    )
                    st.plotly_chart(fig, use_container_width=True)
                except ImportError:
                    st.bar_chart(df_oi, color=["#e74c3c","#2ecc71"])
        else:
            st.info("Waiting for first cycle…")
    with ch2:
        _iv_chart(state.get("iv_chart", {}))

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
