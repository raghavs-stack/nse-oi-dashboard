# 📈 NSE Live OI Dashboard

> **Nifty & BankNifty Options Intelligence — v5.2**
> A real-time Open Interest dashboard with 4-factor signal scoring, trade filtering, RSI+VWAP confirmation, localized PCR, and a full Tkinter GUI — built for serious retail options traders.

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Colab%20%7C%20Windows%20%7C%20Mac%20%7C%20Linux-lightgrey)](#)
[![License](https://img.shields.io/badge/License-MIT-green)](#license)
[![Inspired by](https://img.shields.io/badge/Inspired%20by-haripm2211-orange)](https://github.com/haripm2211/livemarket_option_trading_bot)

---

## 📸 Screenshots

| Terminal / Colab Mode | Tkinter GUI — Signal Tab | Tkinter GUI — Dual OI Tab |
|---|---|---|
| Non-scrolling atomic refresh | Live OI table + signal panel | NIFTY + BANKNIFTY side by side |

---

## ✨ Features

### 🔍 Market Intelligence
| Feature | Description |
|---|---|
| **Localized PCR** | Put-Call Ratio computed on ATM ±8 strikes only — filters far-OTM noise |
| **5-Level Signal** | STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL with exact PCR thresholds |
| **Max Pain** | Real-time max pain calculation (writer loss minimization) |
| **Volume-Weighted OI Score** | `Σ(CE_Chg × Vol) - Σ(PE_Chg × Vol)` — fresh money pressure indicator |
| **Resistance / Support** | From max ΔCE (fresh call wall) and max ΔPE (fresh put floor) — not stale total OI |

### 🧠 Signal Engine
| Feature | Description |
|---|---|
| **4-Factor Bias Vote** | PCR + OI Score + Max Pain + RSI/VWAP → majority vote |
| **RollingRSI (Wilder's)** | Exact Wilder smoothing, 14-period default |
| **RollingVWAP** | Rolling 20-period volume-weighted price |
| **StrategyEngine** | Combines RSI+VWAP for 4th bias vote (ported from haripm2211) |

### 🎯 Trade Filter (max 3 best trades/day)
| Gate | Rule |
|---|---|
| Daily cap | Never more than `MAX_TRADES_PER_DAY` (default: 3) |
| Score gate | Signal must score ≥ `MIN_SIGNAL_SCORE` (default: 55/100) |
| Time gap | Minimum `MIN_TRADE_GAP_MINS` between trades (default: 45 min) |
| Duplicate bias guard | Won't take same direction back-to-back without reversal |

### 📊 Signal Scorer (0–100 pts across 8 factors)
| Factor | Max Points |
|---|---|
| Bias unanimity (all 4 votes agree) | 20 |
| PCR extremity (distance from 1.0) | 15 |
| Net OI score magnitude | 15 |
| RSI+VWAP confirmation | 15 |
| Max Pain alignment | 10 |
| VIX zone (sweet spot 12–18) | 10 |
| Time of day (avoid noise windows) | 10 |
| RoC confirmation | 5 |

### 📉 Charts
- Dark-themed OI profile + ΔOI change panels
- ATM bar highlighted with white border
- ΔOI and Volume annotations per bar
- PCR signal annotation box (color-coded to 5-level signal)
- Max pain, resistance, support lines

### 🖥️ Tkinter GUI (run with `--gui`)
- **Signal Tab**: Live OI treeview + signal panel + trade recommendations
- **Dual OI Tab**: NIFTY + BANKNIFTY simultaneously (NSE_OI_Viewer pattern)
- Expiry dropdown (select weekly/monthly expiry mid-session)
- "New Day / Reset" button
- 60-second auto-refresh with daemon threading

---

## 🚀 Quick Start

### Google Colab
```python
# Paste the entire nse_oi_dashboard.py into a cell and run — auto-installs deps
```

### Local (Terminal mode)
```bash
pip install requests pandas matplotlib yfinance
python nse_oi_dashboard.py
```

### Local (Tkinter GUI)
```bash
python nse_oi_dashboard.py --gui
```

### Force Demo Mode (markets closed)
```bash
python nse_oi_dashboard.py            # auto-detects (default)
# or edit: DEMO_MODE = True
```

---

## ⚙️ Configuration

Edit the `CONFIGURATION` block at the top of `nse_oi_dashboard.py`:

```python
SYMBOL        = "NIFTY"    # "NIFTY" or "BANKNIFTY"
REFRESH_RATE  = 35         # seconds between NSE calls (keep ≥ 35 to avoid blocks)
LOT_SIZE      = 50         # Nifty=50, BankNifty=15

# Trade Filter
MAX_TRADES_PER_DAY  = 3    # hard cap
MIN_SIGNAL_SCORE    = 55   # lower → more trades; raise → stricter quality gate
MIN_TRADE_GAP_MINS  = 45   # spacing between trades

# 5-Level PCR Thresholds (NsePcrSignal exact values)
PCR_THRESHOLDS = {
    "STRONG_BUY":  0.75,
    "BUY":         0.90,
    "SELL":        1.10,
    "STRONG_SELL": 1.30,
}
LOCALIZED_PCR_RANGE = 8    # ±8 strikes around ATM for PCR

# RSI + VWAP
RSI_PERIOD    = 14
VWAP_WINDOW   = 20
RSI_BULLISH   = 55
RSI_BEARISH   = 35

DEMO_MODE     = None       # None=auto | True=force demo | False=force live
```

---

## 📁 Repository Structure

```
nse-oi-dashboard/
├── nse_oi_dashboard.py          # Main file — single-file, self-contained
├── requirements.txt             # Python dependencies
├── .gitignore                   # Ignores logs, charts, backtest CSVs
├── README.md                    # This file
└── samples/
    └── demo_backtest_output.txt # Example EOD backtest report
```

---

## 📊 EOD Backtest

At 15:20 IST (or Ctrl-C), the system automatically runs a backtest on all **taken trades** (skipped signals are excluded):

```
════════════════════════════════════════════════════════════════════════
  EOD BACKTEST REPORT  (TAKEN TRADES ONLY)
  Total signals today: 12  |  Taken: 3  |  Skipped: 9
  Brokerage saved by skipping: ~Rs1,080 est.
════════════════════════════════════════════════════════════════════════
  Time    Sc  Bias      Trade         Type          Entry    Exit         P&L (net)  Result
  -----------------------------------------------------------------------
  10:12   72  BULLISH   22500 CE      Conservative  Rs120.0  Target Hit   Rs+5,960   +
  10:12   72  BULLISH   22550 CE      Moderate      Rs 85.0  Target Hit   Rs+4,210   +
  10:12   72  BULLISH   22600 CE      Aggressive    Rs 42.0  SL Hit       Rs-2,140   -
════════════════════════════════════════════════════════════════════════
  NET GAIN: Rs+8,030  (after ~Rs40/lot brokerage per leg)
  Win Rate: 67%  |  Wins: 2  |  Losses: 1
════════════════════════════════════════════════════════════════════════
```

Saved to `NIFTY_backtest_YYYYMMDD.csv`.

---

## 🔄 Auto-Tuner

PCR thresholds self-adjust every 20 signals based on rolling accuracy:
- Accuracy < 45%: tightens PCR bands (fewer, higher-conviction signals)
- Accuracy > 65%: loosens PCR bands (captures more opportunities)

---

## 🏗️ Architecture

```
NSE API ──► fetch_chain()           Thread-safe (cookie_lock pattern)
              │
              ▼
         build_df()                 Includes volume (totalTradedVolume)
              │
         ┌────┴────────────────────────────────────────────┐
         │                                                  │
     calc_localized_pcr()         calc_max_pain()
     ±8 strikes only              writer loss minimization
         │                                                  │
     generate_pcr_signal()        compute_roc_alerts()
     5-level contrarian                                     │
         │                                                  │
         └────────────────────┬────────────────────────────┘
                              │
                    StrategyEngine.on_tick()
                    RSI(14) + VWAP(20) → tech_signal
                              │
                    4-factor vote
                    [PCR, OI, MaxPain, Tech] → bias
                              │
                    score_signal()           8 factors → 0-100
                              │
                    should_take_trade()      4-gate filter
                              │
                    recommend_strikes()      3 recs (ATM/1OTM/2OTM)
                              │
                    render() / OITkApp       display
```

---

## 🔗 Credits & Inspirations

- **[haripm2211/livemarket_option_trading_bot](https://github.com/haripm2211/livemarket_option_trading_bot)** — NSE session handling, RollingRSI, RollingVWAP, StrategyEngine, NSE_OI_Viewer, NSE_OI_UI workflow, fetch_oi_data_for_ui(), PCR thresholds, plot annotations
- NSE India option chain API (`nseindia.com/api/option-chain-indices`)

---

## ⚠️ Disclaimer

This tool is for **educational and research purposes only**.

- Not SEBI-registered investment advice
- Over 90% of F&O traders incur losses — study before trading live
- Always paper trade first
- Capital at risk: options can expire worthless

**Please trade responsibly. Manage your risk. Stay safe.**

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
