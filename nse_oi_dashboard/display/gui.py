# ════════════════════════════════════════════════════════════════
#  display/gui.py  — Tkinter live dashboard
#  v5.3: adds "IV Analytics" tab (Tab 3) alongside Signal + Dual OI
# ════════════════════════════════════════════════════════════════

import threading
import math
import tkinter as tk
import tkinter.ttk as ttk

import state
from config import (
    SYMBOL, LOT_SIZE, MAX_TRADES_PER_DAY,
    DEMO_MODE, RSI_PERIOD,
)
from core.market_hours import is_market_open, now_ist, next_open_str, is_eod
from core.nse_fetcher   import (
    create_session, fetch_chain, fetch_vix, build_df,
    demo_data, fetch_oi_data_dual, nearest_strike
)
from signals.oi_analytics import (
    calc_max_pain, calc_localized_pcr, generate_pcr_signal,
    compute_roc_alerts, score_signal,
    should_take_trade, register_trade_taken, recommend_strikes,
)
from signals.iv_analytics import interpret_iv
from backtest.eod_backtest import run_eod_backtest


class OITkApp:
    C = {
        "bg": "#0d1117", "panel": "#161b22", "border": "#30363d",
        "sub": "#21262d", "white": "#e6edf3", "muted": "#8b949e",
        "green": "#2ecc71", "red": "#e74c3c", "yellow": "#f1c40f",
        "orange": "#f39c12", "blue": "#3498db",
        "ce_hot": "#2a1515", "pe_hot": "#152a15",
        "atm_bg": "#1a2a1a", "taken": "#0d2a0d",
    }

    def __init__(self, root, process_cycle_fn, iv_tracker, iv_history):
        self.root            = root
        self._process_cycle  = process_cycle_fn
        self._iv_tracker     = iv_tracker
        self._iv_history     = iv_history
        self.root.title(f"NSE OI Dashboard v5.3  [{SYMBOL}]")
        self.root.configure(bg=self.C["bg"])
        self.root.geometry("1500x940")
        self.root.resizable(True, True)

        self._cycle           = 0
        self._use_demo        = (True  if DEMO_MODE is True  else
                                 False if DEMO_MODE is False else
                                 not is_market_open())
        if not self._use_demo:
            create_session()  # nsepython ✓
        self._eod_done        = False
        self._selected_expiry = None
        self._expiry_list     = []
        self._bnf_spot        = 0.0
        self._dual_thread     = None

        self._build()
        self.root.after(800, self._refresh)

    # ── Widget helpers ────────────────────────────────────────────
    def _lbl(self, parent, text, font_size=10, bold=False, fg=None, **kw):
        fw = "bold" if bold else "normal"
        return tk.Label(parent, text=text,
            font=("Courier", font_size, fw),
            bg=kw.pop("bg", self.C["bg"]),
            fg=fg or self.C["white"], **kw)

    # ── Main build ────────────────────────────────────────────────
    def _build(self):
        C = self.C

        # Header
        hdr = tk.Frame(self.root, bg=C["bg"], pady=5)
        hdr.pack(fill="x", padx=12)
        self._lbl(hdr, f"  {SYMBOL}  OI DASHBOARD  v5.3",
                  14, bold=True).pack(side="left")
        mode_txt = "[DEMO]" if self._use_demo else "[LIVE]"
        self.lbl_mode = self._lbl(hdr, mode_txt, 13, bold=True,
            fg=C["yellow"] if self._use_demo else C["green"])
        self.lbl_mode.pack(side="left", padx=12)
        self.lbl_spot = self._lbl(hdr, "Spot: --", 13, bold=True, fg=C["blue"])
        self.lbl_spot.pack(side="left", padx=18)
        self.lbl_vix  = self._lbl(hdr, "VIX: --", 11)
        self.lbl_vix.pack(side="left")
        self.lbl_time = self._lbl(hdr, "--:--:--", 11, fg=C["muted"])
        self.lbl_time.pack(side="right", padx=8)
        self.lbl_cycle = self._lbl(hdr, "Cycle #0", 10, fg=C["muted"])
        self.lbl_cycle.pack(side="right", padx=6)
        other = "BANKNIFTY" if SYMBOL == "NIFTY" else "NIFTY"
        self.lbl_other_spot = self._lbl(hdr, f"{other}: --", 11, fg=C["muted"])
        self.lbl_other_spot.pack(side="right", padx=14)
        self._lbl(hdr, "Expiry:", 10, fg=C["muted"]).pack(side="right", padx=(8, 2))
        self.cmb_expiry = ttk.Combobox(hdr, state="readonly", width=12,
            font=("Courier", 9), values=["auto"])
        self.cmb_expiry.current(0)
        self.cmb_expiry.pack(side="right", padx=4)
        self.cmb_expiry.bind("<<ComboboxSelected>>", lambda _: self._on_expiry_select())

        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")

        # Notebook: 3 tabs
        nb_s = ttk.Style(); nb_s.theme_use("clam")
        nb_s.configure("Dark.TNotebook", background=C["bg"], borderwidth=0)
        nb_s.configure("Dark.TNotebook.Tab", background=C["sub"],
            foreground=C["muted"], font=("Courier", 10, "bold"), padding=[12, 4])
        nb_s.map("Dark.TNotebook.Tab",
            background=[("selected", C["panel"])],
            foreground=[("selected", C["white"])])
        self.notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=8, pady=4)

        # Tab 1 — Signal
        sig_tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(sig_tab, text="  Signal  ")
        self._build_signal_tab(sig_tab)

        # Tab 2 — Dual OI
        dual_tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(dual_tab, text="  Dual OI  ")
        self._build_dual_oi_tab(dual_tab)

        # Tab 3 — IV Analytics (v5.3 NEW)
        iv_tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(iv_tab, text="  IV Analytics  ")
        self._build_iv_tab(iv_tab)

        # Status bar
        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")
        sbar = tk.Frame(self.root, bg=C["panel"], pady=3)
        sbar.pack(fill="x", padx=12)
        self.lbl_status = self._lbl(sbar, "Initializing…", 9,
            fg=C["muted"], bg=C["panel"], anchor="w")
        self.lbl_status.pack(side="left")
        self.lbl_next = self._lbl(sbar, "", 9, fg=C["muted"], bg=C["panel"])
        self.lbl_next.pack(side="right")

    # ── Tab 1: Signal ─────────────────────────────────────────────
    def _build_signal_tab(self, parent):
        C = self.C
        body = tk.Frame(parent, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=6)

        # OI table
        left = tk.Frame(body, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True)
        self._lbl(left, "OPTION CHAIN  (±4% from ATM)", 9,
                  fg=C["muted"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))

        st = ttk.Style(); st.theme_use("clam")
        for nm in ("OI.Treeview", "OI.Treeview.Heading"):
            st.configure(nm, background=C["panel"], foreground=C["white"],
                fieldbackground=C["panel"], font=("Courier", 9))
        st.configure("OI.Treeview", rowheight=23)
        st.configure("OI.Treeview.Heading", background=C["sub"],
            foreground=C["muted"], font=("Courier", 9, "bold"))
        st.map("OI.Treeview", background=[("selected", "#1f6feb")],
               foreground=[("selected", C["white"])])

        cols = ("CE_OI", "CE_Chg", "Strike", "PE_OI", "PE_Chg")
        self.tree = ttk.Treeview(left, columns=cols,
            show="headings", style="OI.Treeview", height=24)
        hdrs = {"CE_OI":"CE OI","CE_Chg":"CE ΔOI","Strike":"Strike",
                "PE_OI":"PE OI","PE_Chg":"PE ΔOI"}
        widths = {"CE_OI":110,"CE_Chg":95,"Strike":90,"PE_OI":110,"PE_Chg":95}
        for c in cols:
            self.tree.heading(c, text=hdrs[c])
            self.tree.column(c, width=widths[c], anchor="center")
        for tag, bg in [("atm",C["atm_bg"]),("ce_hot",C["ce_hot"]),
                         ("pe_hot",C["pe_hot"]),("normal",C["panel"])]:
            self.tree.tag_configure(tag, background=bg)
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        # Right panels
        right = tk.Frame(body, bg=C["bg"], width=640)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        def row(par):
            f = tk.Frame(par, bg=C["panel"]); f.pack(fill="x", padx=8, pady=2)
            return f

        sig_frame = tk.LabelFrame(right, text="  SIGNAL ANALYSIS  ",
            font=("Courier", 10, "bold"), bg=C["panel"],
            fg=C["muted"], bd=1, relief="solid", labelanchor="nw")
        sig_frame.pack(fill="x", pady=(0, 6))

        r1 = row(sig_frame)
        self._lbl(r1,"BIAS:",11,bold=True,fg=C["muted"],bg=C["panel"]).pack(side="left")
        self.lbl_bias = self._lbl(r1,"--",15,bold=True,fg=C["white"],bg=C["panel"])
        self.lbl_bias.pack(side="left",padx=8)
        self.lbl_pcr = self._lbl(r1,"PCR(loc):--",11,fg=C["white"],bg=C["panel"])
        self.lbl_pcr.pack(side="left",padx=16)
        self.lbl_pcr_signal = self._lbl(r1,"NEUTRAL",11,bold=True,fg=C["orange"],bg=C["panel"])
        self.lbl_pcr_signal.pack(side="left",padx=6)
        self.lbl_maxpain = self._lbl(r1,"MaxPain:--",11,fg=C["orange"],bg=C["panel"])
        self.lbl_maxpain.pack(side="left",padx=10)

        r2 = row(sig_frame)
        self.lbl_res = self._lbl(r2,"Res(ΔCE):--",10,fg=C["red"],bg=C["panel"])
        self.lbl_res.pack(side="left")
        self.lbl_sup = self._lbl(r2,"Sup(ΔPE):--",10,fg=C["green"],bg=C["panel"])
        self.lbl_sup.pack(side="left",padx=20)

        r3 = row(sig_frame)
        self._lbl(r3,"SCORE:",10,fg=C["muted"],bg=C["panel"]).pack(side="left")
        self.lbl_score = self._lbl(r3,"0/100",12,bold=True,fg=C["white"],bg=C["panel"])
        self.lbl_score.pack(side="left",padx=6)
        self.score_cv = tk.Canvas(r3,height=14,width=220,
            bg=C["sub"],highlightthickness=0)
        self.score_cv.pack(side="left",padx=4)
        self._score_rect = self.score_cv.create_rectangle(
            0,0,0,14,fill=C["green"],outline="")
        self.lbl_votes = self._lbl(r3,"-- votes",9,fg=C["muted"],bg=C["panel"])
        self.lbl_votes.pack(side="left",padx=6)

        r4 = row(sig_frame)
        self.lbl_breakdown = self._lbl(r4,"Breakdown:--",8,fg=C["muted"],bg=C["panel"])
        self.lbl_breakdown.pack(side="left")

        r5 = row(sig_frame)
        self.lbl_rsi   = self._lbl(r5,f"RSI({RSI_PERIOD}): warming…",10,fg=C["white"],bg=C["panel"])
        self.lbl_rsi.pack(side="left")
        self.lbl_vwap_v = self._lbl(r5,"VWAP: warming…",10,fg=C["white"],bg=C["panel"])
        self.lbl_vwap_v.pack(side="left",padx=16)
        self.lbl_tech = self._lbl(r5,"Tech: NEUTRAL",10,bold=True,fg=C["muted"],bg=C["panel"])
        self.lbl_tech.pack(side="left",padx=16)

        r6 = row(sig_frame)
        self.lbl_filter = self._lbl(r6,"Waiting for first signal…",9,
            fg=C["muted"],bg=C["panel"],wraplength=580,justify="left")
        self.lbl_filter.pack(side="left")

        r7 = row(sig_frame)
        self.lbl_counter = self._lbl(r7,f"Trades today: 0/{MAX_TRADES_PER_DAY}",
            10,fg=C["white"],bg=C["panel"])
        self.lbl_counter.pack(side="left")
        tk.Button(r7,text="New Day / Reset",command=self._reset_day,
            bg="salmon",fg="black",font=("Courier",9),
            relief="flat",padx=6,pady=2).pack(side="right",padx=4)

        # Recommendations
        rec_frame = tk.LabelFrame(right, text="  TRADE RECOMMENDATIONS  ",
            font=("Courier",10,"bold"), bg=C["panel"],
            fg=C["muted"], bd=1, relief="solid", labelanchor="nw")
        rec_frame.pack(fill="x",pady=(0,6))
        hdr_rec = tk.Frame(rec_frame, bg=C["sub"])
        hdr_rec.pack(fill="x",padx=4,pady=(4,0))
        for txt, w in [("#",3),("Strategy",17),("Strike",8),("T",4),
                       ("Prem",7),("SL",7),("Target",8),("1-Lot",9),("R:R",5)]:
            tk.Label(hdr_rec,text=txt,font=("Courier",9,"bold"),
                width=w,bg=C["sub"],fg=C["muted"],anchor="w").pack(side="left")
        self._rec_rows=[]; self._rec_reason=[]
        for i in range(3):
            rf = tk.Frame(rec_frame,bg=C["panel"])
            rf.pack(fill="x",padx=4,pady=1)
            cells={}
            for key,dflt,w in [("num",str(i+1),3),("label","--",17),
                ("strike","--",8),("type","--",4),("prem","--",7),
                ("sl","--",7),("target","--",8),("cost","--",9),("rr","1:2",5)]:
                lb = tk.Label(rf,text=dflt,font=("Courier",9),
                    width=w,bg=C["panel"],fg=C["white"],anchor="w")
                lb.pack(side="left"); cells[key]=lb
            self._rec_rows.append(cells)
            rs = tk.Label(rec_frame,text="",font=("Courier",8),
                bg=C["panel"],fg=C["muted"],anchor="w",padx=10)
            rs.pack(fill="x"); self._rec_reason.append(rs)

        # Signal log
        log_frame = tk.LabelFrame(right, text="  TODAY'S SIGNAL LOG  ",
            font=("Courier",9,"bold"),bg=C["panel"],fg=C["muted"],
            bd=1,relief="solid",labelanchor="nw")
        log_frame.pack(fill="both",expand=True)
        self.log_text = tk.Text(log_frame,height=6,bg=C["panel"],fg=C["white"],
            font=("Courier",8),state="disabled",wrap="none",
            relief="flat",insertbackground=C["white"])
        ysb = tk.Scrollbar(log_frame,orient="vertical",command=self.log_text.yview)
        self.log_text.config(yscrollcommand=ysb.set)
        ysb.pack(side="right",fill="y")
        self.log_text.pack(fill="both",expand=True)

    # ── Tab 2: Dual OI ────────────────────────────────────────────
    def _build_dual_oi_tab(self, parent):
        C = self.C
        parent.columnconfigure(0, weight=1); parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)
        top = tk.Frame(parent, bg=C["bg"])
        top.grid(row=0,column=0,columnspan=2,sticky="ew",padx=10,pady=6)
        self._lbl(top,"NSE OI Viewer  (NIFTY + BANKNIFTY)",12,bold=True,bg=C["bg"]).pack(side="left")
        tk.Button(top,text="Refresh OI",
            command=lambda: self._dual_fetch_threaded(manual=True),
            bg=C["sub"],fg=C["white"],font=("Courier",9),
            relief="flat",padx=8,pady=3).pack(side="right",padx=10)
        self._dual_status = self._lbl(top,"Fetching…",9,fg=C["muted"],bg=C["bg"])
        self._dual_status.pack(side="right",padx=6)

        for col, sym, hdr_attr, sup_attr, res_attr, tree_attr in [
            (0,"NIFTY","_dual_nf_hdr","_dual_nf_sup","_dual_nf_res","_dual_nf_tree"),
            (1,"BANKNIFTY","_dual_bnf_hdr","_dual_bnf_sup","_dual_bnf_res","_dual_bnf_tree"),
        ]:
            frm = tk.Frame(parent,bg=C["panel"],bd=1,relief="solid")
            frm.grid(row=1,column=col,sticky="nsew",
                padx=(10 if col==0 else 5,5 if col==0 else 10),pady=5)
            frm.columnconfigure(0,weight=1); frm.rowconfigure(2,weight=1)

            hdr = self._lbl(frm,f"{sym}  |  LTP: --",10,bold=True,
                fg=C["blue"],bg=C["panel"])
            hdr.grid(row=0,column=0,sticky="w",padx=8,pady=(6,2))
            setattr(self,hdr_attr,hdr)

            sr = tk.Frame(frm,bg=C["panel"])
            sr.grid(row=1,column=0,sticky="ew",padx=8,pady=(0,4))
            sup_lbl = self._lbl(sr,"Support: --",9,fg=C["green"],bg=C["panel"])
            sup_lbl.pack(side="left")
            setattr(self,sup_attr,sup_lbl)
            res_lbl = self._lbl(sr,"Resistance: --",9,fg=C["red"],bg=C["panel"])
            res_lbl.pack(side="left",padx=20)
            setattr(self,res_attr,res_lbl)

            tree = self._make_dual_tree(frm)
            tree.grid(row=2,column=0,sticky="nsew",padx=4,pady=4)
            setattr(self,tree_attr,tree)

    def _make_dual_tree(self, parent):
        C = self.C
        t = ttk.Treeview(parent, columns=("CE_OI","Strike","PE_OI"),
            show="headings", style="OI.Treeview", height=18)
        for col,w,txt in [("CE_OI",120,"CE OI"),("Strike",90,"Strike"),("PE_OI",120,"PE OI")]:
            t.heading(col,text=txt); t.column(col,width=w,anchor="center")
        t.tag_configure("atm",background=C["atm_bg"])
        t.tag_configure("ce_hot",background=C["ce_hot"])
        t.tag_configure("pe_hot",background=C["pe_hot"])
        t.tag_configure("normal",background=C["panel"])
        return t

    # ── Tab 3: IV Analytics (v5.3 NEW) ───────────────────────────
    def _build_iv_tab(self, parent):
        C = self.C
        parent.columnconfigure(0, weight=1); parent.columnconfigure(1, weight=1)

        # ── Left: Live IV metrics ─────────────────────────────────
        left = tk.LabelFrame(parent, text="  LIVE IV METRICS  ",
            font=("Courier",11,"bold"), bg=C["panel"], fg=C["muted"],
            bd=1, relief="solid", labelanchor="nw")
        left.grid(row=0, column=0, sticky="nsew", padx=(10,5), pady=10)

        def row(lbl_text):
            f = tk.Frame(left, bg=C["panel"])
            f.pack(fill="x", padx=12, pady=4)
            self._lbl(f, f"{lbl_text}:", 10, bold=True,
                fg=C["muted"], bg=C["panel"]).pack(side="left", width=12)
            val = self._lbl(f, "--", 14, bold=True,
                fg=C["white"], bg=C["panel"])
            val.pack(side="left", padx=8)
            return val

        self.iv_lbl_atm     = row("ATM IV")
        self.iv_lbl_ivr     = row("IVR")
        self.iv_lbl_ivp     = row("IVP")
        self.iv_lbl_regime  = row("Regime")
        self.iv_lbl_hint    = self._lbl(left, "--", 9, fg=C["muted"],
            bg=C["panel"], wraplength=380, justify="left")
        self.iv_lbl_hint.pack(fill="x", padx=12, pady=(0,8))

        # Daily IV section
        tk.Frame(left, bg=C["border"], height=1).pack(fill="x", padx=8)
        self._lbl(left, "Daily IV (intraday session):", 10, bold=True,
            fg=C["muted"], bg=C["panel"]).pack(anchor="w", padx=12, pady=(8,2))

        for attr, lbl in [("iv_lbl_open","Open"), ("iv_lbl_high","High"),
                          ("iv_lbl_low","Low"), ("iv_lbl_avg","Avg"),
                          ("iv_lbl_current","Current")]:
            f = tk.Frame(left, bg=C["panel"])
            f.pack(fill="x", padx=12, pady=2)
            self._lbl(f, f"{lbl}:", 10, fg=C["muted"],
                bg=C["panel"]).pack(side="left", width=10)
            lbl_w = self._lbl(f, "--", 12, bold=True,
                fg=C["white"], bg=C["panel"])
            lbl_w.pack(side="left", padx=6)
            setattr(self, attr, lbl_w)

        self.iv_spike_lbl = self._lbl(left, "", 10, bold=True,
            fg=C["red"], bg=C["panel"])
        self.iv_spike_lbl.pack(anchor="w", padx=12, pady=4)

        # Historical range
        tk.Frame(left, bg=C["border"], height=1).pack(fill="x", padx=8)
        self._lbl(left, "52-Week IV Range:", 10, bold=True,
            fg=C["muted"], bg=C["panel"]).pack(anchor="w", padx=12, pady=(8,2))
        for attr, lbl in [("iv_hist_high","52w High"), ("iv_hist_low","52w Low"),
                          ("iv_hist_avg","52w Avg"), ("iv_data_days","History")]:
            f = tk.Frame(left, bg=C["panel"])
            f.pack(fill="x", padx=12, pady=2)
            self._lbl(f, f"{lbl}:", 10, fg=C["muted"],
                bg=C["panel"]).pack(side="left", width=12)
            lbl_w = self._lbl(f, "--", 11, fg=C["white"], bg=C["panel"])
            lbl_w.pack(side="left", padx=6)
            setattr(self, attr, lbl_w)

        # ── Right: IV Skew ────────────────────────────────────────
        right = tk.LabelFrame(parent, text="  IV SKEW — WHICH SIDE IS HEAVY  ",
            font=("Courier",11,"bold"), bg=C["panel"], fg=C["muted"],
            bd=1, relief="solid", labelanchor="nw")
        right.grid(row=0, column=1, sticky="nsew", padx=(5,10), pady=10)

        # Big direction badge
        self.iv_skew_dir_lbl = tk.Label(right, text="N/A",
            font=("Courier", 28, "bold"), bg=C["panel"], fg=C["muted"])
        self.iv_skew_dir_lbl.pack(pady=(20, 4))

        self.iv_skew_pct_lbl = tk.Label(right, text="Skew: --",
            font=("Courier", 16), bg=C["panel"], fg=C["white"])
        self.iv_skew_pct_lbl.pack(pady=4)

        # OTM detail
        detail = tk.Frame(right, bg=C["panel"])
        detail.pack(fill="x", padx=20, pady=8)
        for attr, lbl in [("iv_otm_put_lbl","OTM Put IV"),
                          ("iv_otm_call_lbl","OTM Call IV"),
                          ("iv_skew_hint_lbl","Interpretation")]:
            f = tk.Frame(detail, bg=C["panel"])
            f.pack(fill="x", pady=3)
            self._lbl(f, f"{lbl}:", 10, fg=C["muted"],
                bg=C["panel"]).pack(side="left", width=18)
            lb = self._lbl(f, "--", 12, bold=True,
                fg=C["white"], bg=C["panel"])
            lb.pack(side="left", padx=6)
            setattr(self, attr, lb)

        # Strategy hint
        tk.Frame(right, bg=C["border"], height=1).pack(fill="x", padx=8, pady=8)
        self._lbl(right, "Strategy Hint:", 10, bold=True,
            fg=C["muted"], bg=C["panel"]).pack(anchor="w", padx=12)
        self.iv_strategy_lbl = self._lbl(right, "--", 10,
            fg=C["white"], bg=C["panel"], wraplength=360, justify="left")
        self.iv_strategy_lbl.pack(anchor="w", padx=12, pady=4)

    # ── Refresh loop ──────────────────────────────────────────────
    def _refresh(self):
        self._cycle += 1
        self.lbl_time.config(text=now_ist().strftime("%H:%M:%S IST"))
        self.lbl_cycle.config(text=f"Cycle #{self._cycle}")

        def _bg():
            try:
                if self._use_demo:
                    import math, random
                    vix  = str(round(14.5 + math.sin(self._cycle * 0.5) * 2.5
                                     + random.uniform(-0.3, 0.3), 2))
                    data = demo_data(SYMBOL, self._cycle)
                else:
                    vix  = fetch_vix()
                    data = fetch_chain(None, SYMBOL)
                    if data is None:
                        self.root.after(0, lambda: self.lbl_status.config(
                            text=f"No data — retrying in 30s"))
                        return
                    if self._cycle % 10 == 0:
                        pass  # nsepython manages its own session

                sig = self._process_cycle(
                    data, SYMBOL, vix, self._use_demo, self._cycle,
                    selected_expiry=self._selected_expiry)

                if sig:
                    state.signal_log.append(sig)
                    if len(state.signal_log) >= 2:
                        prev = state.signal_log[-2]
                        prev.spot_exit = sig.spot
                        from signals.oi_analytics import auto_tune
                        auto_tune(prev)

                # EOD backtest
                if is_eod() and not self._eod_done and not self._use_demo:
                    self._eod_done = True
                    final_spot = data["records"]["underlyingValue"]
                    for s in state.signal_log:
                        if s.spot_exit is None: s.spot_exit = final_spot
                    run_eod_backtest(final_spot)
                    self._iv_history.update(self._iv_tracker._summary().get("current") or 0)

                # Update expiry combobox
                avail = data["records"].get("expiryDates", [])
                if avail:
                    self.root.after(0, lambda a=avail: self._update_expiry_combo(a))

                # Update UI on main thread
                if sig:
                    self.root.after(0, lambda s=sig, d=data, v=vix:
                        self._update_ui(s, d, v))

            except Exception as e:
                self.root.after(0, lambda: self.lbl_status.config(
                    text=f"Error: {e}"))

        t = threading.Thread(target=_bg, daemon=True)
        t.start()

        interval = 5000 if self._use_demo else (self.root.after.__self__
            if hasattr(self.root.after, "__self__") else 35) * 1000
        from config import REFRESH_RATE
        self.root.after(5000 if self._use_demo else REFRESH_RATE * 1000, self._refresh)

    def _update_ui(self, sig, data, vix):
        C = self.C

        # Header
        self.lbl_spot.config(text=f"Spot: Rs{sig.spot:,.2f}")
        self.lbl_vix.config(text=f"VIX: {vix}")

        # Signal tab
        bias_colors = {"BULLISH": C["green"], "BEARISH": C["red"], "NEUTRAL": C["orange"]}
        self.lbl_bias.config(text=sig.bias, fg=bias_colors.get(sig.bias, C["white"]))

        rec  = data["records"]
        avail = rec.get("expiryDates", [])
        expiry = (self._selected_expiry if self._selected_expiry and self._selected_expiry in avail
                  else avail[0] if avail else "")
        df   = build_df(rec["data"], expiry)

        if not df.empty:
            local_pcr   = calc_localized_pcr(df, sig.spot)
            pcr_sig, pcr_color = generate_pcr_signal(local_pcr)
            max_pain    = calc_max_pain(df)
            resistance  = int(df.loc[df["CE_Chg"].idxmax(), "Strike"])
            support     = int(df.loc[df["PE_Chg"].idxmax(), "Strike"])

            self.lbl_pcr.config(
                text=f"PCR(loc): {local_pcr:.3f}" if local_pcr else "PCR(loc): N/A")
            self.lbl_pcr_signal.config(text=pcr_sig, fg=pcr_color)
            self.lbl_maxpain.config(text=f"MaxPain: Rs{int(max_pain):,}")
            self.lbl_res.config(text=f"Res(ΔCE): Rs{resistance:,}")
            self.lbl_sup.config(text=f"Sup(ΔPE): Rs{support:,}")

            # Populate OI tree
            for row in self.tree.get_children():
                self.tree.delete(row)
            atm = nearest_strike(sig.spot, SYMBOL)
            max_ce = df["CE_OI"].max(); max_pe = df["PE_OI"].max()
            for _, r in df.iterrows():
                s = int(r["Strike"])
                tags = ["atm"] if s == atm else (
                    ["ce_hot"] if r["CE_OI"] >= 0.8 * max_ce else
                    ["pe_hot"] if r["PE_OI"] >= 0.8 * max_pe else ["normal"])
                self.tree.insert("", "end", values=(
                    f"{int(r['CE_OI']):,}", f"{int(r['CE_Chg']):+,}",
                    f"{s:,}",
                    f"{int(r['PE_OI']):,}", f"{int(r['PE_Chg']):+,}",
                ), tags=tags)

            # IV tab update
            self._update_iv_tab(df, sig)

        # Score bar
        self.lbl_score.config(text=f"{sig.score}/100")
        self.score_cv.coords(self._score_rect,
            0, 0, int(sig.score / 100 * 220), 14)
        self.lbl_votes.config(
            text="UNANIMOUS" if sig.votes_unanimous else "MAJORITY")

        if sig.rsi:
            self.lbl_rsi.config(text=f"RSI({RSI_PERIOD}): {sig.rsi:.1f}")
            self.lbl_vwap_v.config(text=f"VWAP: Rs{sig.vwap:.1f}" if sig.vwap else "VWAP: --")
            tech_clr = bias_colors.get(sig.tech_signal, C["muted"])
            self.lbl_tech.config(text=f"Tech: {sig.tech_signal}", fg=tech_clr)

        filter_txt = (f">> TRADE TAKEN ({state.daily_trades_taken}/{MAX_TRADES_PER_DAY})"
                      if sig.taken else
                      f"-- SKIPPED: {sig.skip_reason}")
        self.lbl_filter.config(
            text=filter_txt,
            fg=C["green"] if sig.taken else C["muted"])
        self.lbl_counter.config(
            text=f"Trades today: {state.daily_trades_taken}/{MAX_TRADES_PER_DAY}")

        # Recommendations
        recs_list = [sig.rec1, sig.rec2, sig.rec3]
        for i, (cells, reason_lbl) in enumerate(
                zip(self._rec_rows, self._rec_reason)):
            r = recs_list[i]
            cells["label"].config(text=r.label[:17])
            cells["strike"].config(text=f"{r.strike:,}")
            cells["type"].config(text=r.opt_type,
                fg=C["red"] if r.opt_type == "CE" else C["green"])
            cells["prem"].config(text=f"Rs{r.premium:.1f}")
            cells["sl"].config(text=f"Rs{r.sl:.1f}")
            cells["target"].config(text=f"Rs{r.target:.1f}")
            cells["cost"].config(text=f"Rs{r.lot_cost:,.0f}")
            reason_lbl.config(text=f"  → {r.reason}")

        # Signal log
        self.log_text.config(state="normal")
        self.log_text.delete("1.0","end")
        for s in reversed(state.signal_log[-30:]):
            icon = "✓" if s.taken else "✗"
            iv_str = f"IV:{s.atm_iv:.1f}%" if s.atm_iv else ""
            self.log_text.insert("end",
                f"{icon} {s.time} {s.bias:<9} score:{s.score:>3} "
                f"PCR:{s.pcr:.3f} {iv_str} {s.skip_reason}\n")
        self.log_text.config(state="disabled")

        self.lbl_status.config(text=f"Last update: {now_ist().strftime('%H:%M:%S')}")
        self.lbl_next.config(
            text=f"Next open: {next_open_str()}" if not is_market_open() else "")

    def _update_iv_tab(self, df, sig):
        """Update IV Analytics tab with latest metrics."""
        C = self.C
        atm_iv = sig.atm_iv
        ivr    = sig.ivr
        ivp    = sig.ivp

        if atm_iv:
            self.iv_lbl_atm.config(text=f"{atm_iv:.2f}%")
        self.iv_lbl_ivr.config(text=f"{ivr:.1f}" if ivr else "Building…")
        self.iv_lbl_ivp.config(text=f"{ivp:.1f}" if ivp else "Building…")

        interp = interpret_iv(ivr, ivp, atm_iv)
        regime_colors = {"HIGH IV": C["red"], "LOW IV": C["green"], "NORMAL IV": C["orange"]}
        self.iv_lbl_regime.config(text=interp["regime"],
            fg=regime_colors.get(interp["regime"], C["white"]))
        self.iv_lbl_hint.config(text=interp["strategy_hint"])
        self.iv_strategy_lbl.config(text=interp["strategy_hint"])

        # Daily IV
        daily = self._iv_tracker.record(atm_iv)
        for attr, key in [("iv_lbl_open","open"),("iv_lbl_high","high"),
                          ("iv_lbl_low","low"),("iv_lbl_avg","avg"),
                          ("iv_lbl_current","current")]:
            val = daily.get(key)
            getattr(self, attr).config(
                text=f"{val:.2f}%" if val is not None else "warming…")
        spike = daily.get("spike_alert", False)
        self.iv_spike_lbl.config(
            text="⚠  IV SPIKE ALERT: sharp rise detected!" if spike else "")

        # Historical
        hist = self._iv_history.summary(atm_iv) if atm_iv else {}
        self.iv_hist_high.config(
            text=f"{hist['hist_high']:.2f}%" if hist.get("hist_high") else "--")
        self.iv_hist_low.config(
            text=f"{hist['hist_low']:.2f}%" if hist.get("hist_low") else "--")
        self.iv_hist_avg.config(
            text=f"{hist['hist_avg']:.2f}%" if hist.get("hist_avg") else "--")
        self.iv_data_days.config(text=f"{hist.get('data_days',0)} days")

        # Skew
        skew = calc_iv_skew_from_sig(df, sig.spot)
        dir_colors = {"PUT HEAVY": C["red"], "CALL HEAVY": C["green"],
                      "BALANCED": C["orange"]}
        self.iv_skew_dir_lbl.config(
            text=skew.get("direction","N/A"),
            fg=dir_colors.get(skew.get("direction",""), C["muted"]))
        pct = skew.get("skew_pct")
        self.iv_skew_pct_lbl.config(
            text=f"Skew: {pct:+.2f}%" if pct is not None else "Skew: N/A")
        self.iv_otm_put_lbl.config(
            text=f"{skew['otm_put_iv']:.2f}%" if skew.get("otm_put_iv") else "--")
        self.iv_otm_call_lbl.config(
            text=f"{skew['otm_call_iv']:.2f}%" if skew.get("otm_call_iv") else "--")

        hints = {
            "PUT HEAVY": "OTM puts expensive → market fears downside; put sellers charging premium",
            "CALL HEAVY": "OTM calls expensive → market fears upside breakout / short squeeze",
            "BALANCED":   "Symmetric IV smile → no strong directional skew bias",
        }
        self.iv_skew_hint_lbl.config(text=hints.get(skew.get("direction",""), "--"))

    # ── Dual OI helpers ───────────────────────────────────────────
    def _dual_fetch_threaded(self, manual=False):
        t = threading.Thread(target=self._dual_fetch_worker, daemon=True)
        t.start()
        self._dual_thread = t
        self.root.after(100, lambda: self._dual_check_thread(t))

    def _dual_check_thread(self, thread):
        if thread.is_alive():
            self.root.after(100, lambda: self._dual_check_thread(thread))
        else:
            self._dual_status.config(
                text=f"Updated {now_ist().strftime('%H:%M:%S')}")
        self.root.after(60000, lambda: self._dual_fetch_threaded())

    def _dual_fetch_worker(self):
        result = fetch_oi_data_dual()
        if result:
            self.root.after(0, lambda r=result: self._dual_update_ui(r))

    def _dual_update_ui(self, result):
        nf  = result.get("NIFTY",    {})
        bnf = result.get("BANKNIFTY",{})
        self._bnf_spot = bnf.get("ltp", 0)
        other = "BANKNIFTY" if SYMBOL == "NIFTY" else "NIFTY"
        self.lbl_other_spot.config(
            text=f"{other}: Rs{self._bnf_spot:,.2f}" if self._bnf_spot else f"{other}: --")
        if nf:
            self._dual_nf_hdr.config(
                text=f"Nifty 50  |  LTP: Rs{nf['ltp']:,.2f}"
                     f"  |  Nearest: {nf['nearest_strike']:,}")
            self._dual_nf_sup.config(text=f"Support: {nf['max_support']:,}")
            self._dual_nf_res.config(text=f"Resistance: {nf['max_resistance']:,}")
            self._populate_dual_tree(self._dual_nf_tree, nf["oi_data"],
                                     nf["nearest_strike"])
        if bnf:
            self._dual_bnf_hdr.config(
                text=f"Bank Nifty  |  LTP: Rs{bnf['ltp']:,.2f}"
                     f"  |  Nearest: {bnf['nearest_strike']:,}")
            self._dual_bnf_sup.config(text=f"Support: {bnf['max_support']:,}")
            self._dual_bnf_res.config(text=f"Resistance: {bnf['max_resistance']:,}")
            self._populate_dual_tree(self._dual_bnf_tree, bnf["oi_data"],
                                     bnf["nearest_strike"])

    def _populate_dual_tree(self, tree, data_list, nearest_strike):
        for row in tree.get_children():
            tree.delete(row)
        if not data_list:
            return
        max_ce = max((d["ce_oi"] for d in data_list), default=1)
        max_pe = max((d["pe_oi"] for d in data_list), default=1)
        for d in data_list:
            s   = int(d["strike"])
            tag = ("atm"    if s == nearest_strike else
                   "ce_hot" if d["ce_oi"] >= 0.8 * max_ce else
                   "pe_hot" if d["pe_oi"] >= 0.8 * max_pe else "normal")
            tree.insert("", "end",
                values=(f"{d['ce_oi']:,}", f"{s:,}", f"{d['pe_oi']:,}"),
                tags=(tag,))

    # ── Expiry helpers ────────────────────────────────────────────
    def _on_expiry_select(self):
        sel = self.cmb_expiry.get()
        self._selected_expiry = None if sel == "auto" else sel

    def _update_expiry_combo(self, avail):
        current = self.cmb_expiry.get()
        values  = ["auto"] + list(avail)
        self.cmb_expiry["values"] = values
        if current not in values:
            self.cmb_expiry.current(0)
            self._selected_expiry = None

    # ── Reset ─────────────────────────────────────────────────────
    def _reset_day(self):
        state.reset_day()
        self._iv_tracker.reset()
        self._eod_done        = False
        self._selected_expiry = None
        self.cmb_expiry.current(0)
        self.log_text.config(state="normal")
        self.log_text.delete("1.0","end")
        self.log_text.config(state="disabled")
        self.lbl_status.config(text="Day reset. Counters cleared.")


def calc_iv_skew_from_sig(df, spot):
    """Thin wrapper so gui.py doesn't import iv_analytics directly."""
    from signals.iv_analytics import calc_iv_skew
    return calc_iv_skew(df, spot)
