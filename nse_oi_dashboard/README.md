# 📈 NSE Live OI Dashboard  v5.3

> **Nifty & BankNifty Options Intelligence — Modular Architecture**
> Real-time Open Interest + Implied Volatility analytics for serious retail options traders.

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Colab%20%7C%20Windows%20%7C%20Mac%20%7C%20Linux-lightgrey)](#)
[![License](https://img.shields.io/badge/License-MIT-green)](#license)
[![Inspired by](https://img.shields.io/badge/Forked%20from-haripm2211-orange)](https://github.com/haripm2211/livemarket_option_trading_bot)

---

## 📸 Screenshots

| Terminal / Colab | Signal Tab (GUI) | IV Analytics Tab (GUI) |
|---|---|---|
| Atomic non-scrolling refresh | Live OI table + signal panel | ATM IV · IVR · IVP · IV Skew |

---

## ✨ What's New in v5.3 — IV Analytics

| Metric | Description | Source |
|---|---|---|
| **ATM IV** | Live implied volatility at ATM strike (CE+PE avg) | NSE `impliedVolatility` field |
| **IVR** | IV Rank — where current IV sits in 52-week range (0–100) | yfinance VIX bootstrap |
| **IVP** | IV Percentile — % of days last year IV was below today | Rolling CSV history |
| **Daily IV** | Session high / low / avg / current IV + spike alert | In-session tracking |
| **IV Skew %** | `OTM Put IV − OTM Call IV` | 3 strikes each side of ATM |
| **Skew Side** | PUT HEAVY / CALL HEAVY / BALANCED | Threshold ±2% |
| **IV Regime** | HIGH IV / NORMAL IV / LOW IV with strategy hint | IVR-based classification |
| **IV Skew Chart** | 3rd chart panel — smile curve (CE IV + PE IV vs strike) | Matplotlib overlay |

---

## 📁 Repository Structure

```
nse-oi-dashboard/
├── main.py                     # Entry point (terminal + GUI)
├── config.py                   # All constants — edit only this file
├── state.py                    # Shared mutable session state
├── requirements.txt
├── README.md
├── .gitignore
├── LICENSE
│
├── core/
│   ├── market_hours.py         # IST timezone, is_market_open(), next_open_str()
│   └── nse_fetcher.py          # NSE session, build_df (w/ CE_IV/PE_IV), demo_data
│
├── signals/
│   ├── indicators.py           # RollingRSI, RollingVWAP, StrategyEngine
│   ├── oi_analytics.py         # Max pain, localized PCR, 5-level signal,
│   │                           # RoC alerts, 8-factor scorer, trade filter
│   └── iv_analytics.py         # ★ NEW: ATM IV, IVR, IVP, daily IV,
│                               #         IV Skew, IVTracker, IVHistory
│
├── display/
│   ├── terminal.py             # Non-scrolling render (Colab + local)
│   └── gui.py                  # Tkinter GUI: Signal + Dual OI + IV Analytics tabs
│
└── backtest/
    └── eod_backtest.py         # Signal + TradeRec dataclasses, EOD backtest
```

---

## 🚀 Quick Start

### Google Colab
```python
# Upload all files, then in a cell:
import subprocess
subprocess.run(["pip", "install", "-q", "yfinance", "requests", "pandas", "matplotlib"])
exec(open("main.py").read())
```

### Local — Terminal mode (default)
```bash
pip install -r requirements.txt
python main.py
```

### Local — Tkinter GUI
```bash
python main.py --gui
```

---

## ⚙️ Configuration (config.py)

```python
SYMBOL        = "NIFTY"     # "NIFTY" or "BANKNIFTY"
LOT_SIZE      = 50          # Nifty=50, BankNifty=15
REFRESH_RATE  = 35          # seconds between NSE calls (keep ≥ 35)

# Trade Filter
MAX_TRADES_PER_DAY  = 3
MIN_SIGNAL_SCORE    = 55    # 0-100; lower = more trades
MIN_TRADE_GAP_MINS  = 45

# IV Analytics (v5.3)
IV_SKEW_OTM_STRIKES          = 3     # strikes each side of ATM for skew
IV_SKEW_PUT_HEAVY_THRESHOLD  = 2.0   # skew % above this = PUT HEAVY
IV_SKEW_CALL_HEAVY_THRESHOLD = -2.0  # skew % below this = CALL HEAVY
IV_HISTORY_DAYS              = 252   # 1 trading year for IVR/IVP
IV_DAILY_ALERT_SPIKE         = 20    # % rise in one cycle = spike alert

DEMO_MODE     = None         # None=auto | True=force demo | False=force live
```

---

## 📊 IV Analytics Deep-Dive

### ATM IV
NSE's option chain API provides `impliedVolatility` for each CE and PE strike.
ATM IV is the average of CE_IV and PE_IV at the nearest-ATM strike.

### IVR — IV Rank
```
IVR = (current_IV - 52w_low) / (52w_high - 52w_low) × 100
```
- **IVR > 50**: IV elevated — selling premium has historical edge
- **IVR < 25**: IV cheap — buying premium is relatively inexpensive

### IVP — IV Percentile
```
IVP = (days in past year where IV < current_IV) / total_days × 100
```
- Complements IVR by showing how frequently IV was this high

### IV History Storage
First run bootstraps from yfinance India VIX (`^INDIAVIX`) for 252 days
and saves to `SYMBOL_iv_history.csv`. Each subsequent session appends the
closing ATM IV, building a true per-strike history over time.

### IV Skew
```
skew = avg(OTM Put IV, n strikes) − avg(OTM Call IV, n strikes)

PUT HEAVY  (skew > +2%): market fears downside; put sellers charge premium
CALL HEAVY (skew < -2%): market fears upside breakout / short squeeze
BALANCED   (|skew| ≤ 2%): symmetric demand on both sides
```

---

## 🏗️ Architecture

```
NSE API
  │
  ▼
core/nse_fetcher.py          ─── build_df() with CE_IV / PE_IV columns
  │
  ├── signals/oi_analytics.py  ─── max pain, localized PCR, score, filter
  ├── signals/iv_analytics.py  ─── ATM IV, IVR, IVP, daily, skew  ★ v5.3
  └── signals/indicators.py   ─── RSI(14), VWAP(20), StrategyEngine
  │
  ▼
main.process_cycle()           ─── assembles all signals + iv_data dict
  │
  ├── display/terminal.py      ─── 3-panel chart (OI + ΔOI + IV Skew curve)
  └── display/gui.py           ─── 3-tab Tkinter: Signal | Dual OI | IV Analytics
```

---

## 🔗 Credits

- **[haripm2211/livemarket_option_trading_bot](https://github.com/haripm2211/livemarket_option_trading_bot)** — RSI+VWAP, StrategyEngine, NSE_OI_Viewer, thread-safe cookie pattern, PCR thresholds, plot patterns
- NSE India option chain API (`nseindia.com/api/option-chain-indices`)
- IV interpretation conventions: Sensibull / Opstra platform methodology

---

## ⚠️ Disclaimer

Educational and research purposes only. Not SEBI-registered investment advice.
Over 90% of F&O traders incur losses. Always paper-trade first.

## 📄 License

MIT — see [LICENSE](LICENSE)
