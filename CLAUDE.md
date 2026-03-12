# NSE OI Dashboard — Claude Code Project Context

## Project Identity
**NSE Live Open Interest Dashboard v5.6**
Real-time NIFTY/BANKNIFTY options analytics for intraday traders.
Python • Shoonya (Finvasia) broker API • Streamlit • Tkinter • Telegram

## Architecture (23 files, fully modular)

```
nse_oi_dashboard/
├── main.py                  # Entry point + main loop (process_cycle)
├── config.py                # ALL constants — edit here first
├── state.py                 # Shared mutable state (signal_log, prev_oi, etc.)
├── credentials_template.py  # Copy → credentials.py (git-ignored)
├── streamlit_app.py         # Browser dashboard (streamlit run streamlit_app.py)
│
├── core/
│   ├── market_hours.py      # IST timezone, is_market_open(), now_ist()
│   ├── nse_fetcher.py       # Data orchestration, strike helpers
│   └── shoonya_client.py    # Shoonya API: login, spot, option chain
│
├── signals/
│   ├── indicators.py        # RSI, VWAP, price history
│   ├── oi_analytics.py      # PCR, max pain, signal scorer (9 factors, 0-100)
│   ├── iv_analytics.py      # ATM IV, IVR, IVP, IV skew (252d rolling)
│   └── advanced_analytics.py # GEX, SmartMoney, Flow, Momentum, Dealer, Breakout
│
├── display/
│   ├── terminal.py          # Non-scrolling terminal render
│   └── gui.py               # Tkinter 3-tab GUI
│
├── backtest/
│   └── eod_backtest.py      # EOD signal backtest + TradeRec dataclass
│
└── alerts/
    └── telegram_alerts.py   # Push alerts: signal, breakout, RoC, EOD
```

## Critical Domain Rules

### Market Hours
- NSE trades 09:15–15:30 IST, Monday–Friday
- API returns empty `{}` outside these hours — this is **normal**
- All testing must account for market hours
- `is_market_open()` in `core/market_hours.py` is the authority

### Data Source
- **Shoonya (Finvasia) broker API** — free, official, no bot-blocking
- NIFTY spot token: `26000`, BANKNIFTY: `26009`, FINNIFTY: `26037`
- VIX token: `26017`
- Option chain: `get_option_chain()` → per-token `get_quotes()`
- `credentials.py` (git-ignored) holds all API credentials
- NSE direct scraping is **permanently blocked** by Akamai — never attempt

### Signal Architecture
- **5-vote system**: PCR, OI Score, Max Pain, RSI/VWAP, Dealer Hedge
- **9-factor scorer** (0–100 pts): unanimity(20) + pcr(15) + oi_score(15) + max_pain(10) + vix(10) + time(10) + roc(5) + rsi_vwap(10) + dealer(5)
- **4-gate trade filter**: daily cap, min score, min gap, no repeat bias
- Bias: BULLISH | BEARISH | NEUTRAL
- Min signal score for trade: `MIN_SIGNAL_SCORE` in config.py (default 55)

### DataFrame Columns (canonical)
| Column | Description |
|--------|-------------|
| Strike | Strike price (float) |
| CE_OI / PE_OI | Call/Put open interest (lots) |
| CE_Chg / PE_Chg | OI change this cycle |
| CE_Vol / PE_Vol | Volume this cycle |
| CE_IV / PE_IV | Implied volatility (%) |
| CE_LTP / PE_LTP | Last traded price |

### Key Config Variables (config.py)
- `SYMBOL` — "NIFTY" | "BANKNIFTY" | "FINNIFTY"
- `REFRESH_RATE` — seconds between cycles (default 35)
- `LOT_SIZE` — 50 for NIFTY, 15 for BANKNIFTY
- `NUM_STRIKES` — option chain depth (default 20 each side)
- `DISPLAY_MODE` — "terminal" | "tkinter"
- `PCR_THRESHOLDS` — dict of STRONG_BUY/BUY/SELL/STRONG_SELL levels

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill credentials
cp credentials_template.py credentials.py
# Edit credentials.py with Shoonya details

# Live dashboard (terminal)
python main.py

# Live dashboard (Tkinter GUI)
python main.py --gui

# Streamlit browser dashboard
streamlit run streamlit_app.py
# → open http://localhost:8501

# Demo mode (outside market hours, uses sample data)
python main.py --demo
```

## State Management Rules
- `state.py` is the **only** source of mutable global state
- Never add new module-level mutable variables outside `state.py`
- `state.signal_log` — list of Signal namedtuples (persisted intraday)
- `state.prev_oi` — dict for RoC calculation between cycles
- `state.accuracy_window` — rolling 20-cycle accuracy for auto-tuner

## Testing Protocol
- Run syntax check: `python3 -c "import ast; ast.parse(open('file.py').read())"`
- Batch check all: `python3 -c "import ast,os; [print('OK',f) for r,d,files in os.walk('.') for f in files if f.endswith('.py') and not ast.parse(open(os.path.join(r,f)).read())]"`
- Never test data fetch during market hours without live credentials
- Use `--demo` flag for offline testing

## Security Rules
- `credentials.py` is in `.gitignore` — **never commit it**
- No secrets in any other file — use `credentials.py` exclusively
- Shoonya password is SHA-256 hashed before sending (done in shoonya_client.py)
- Telegram token/chat_id in `credentials.py` only

## Agent Delegation Guide
| Task | Use Agent |
|------|-----------|
| New analytics module | `nse-analytics-agent` |
| Debugging data fetch | `nse-data-agent` |
| Signal logic changes | `nse-signal-agent` |
| Display/UI changes | `nse-display-agent` |
| Performance review | `code-reviewer` |
| Security check | `security-reviewer` |

## Common Pitfalls
1. **Empty data outside market hours** — not a bug, expected behaviour
2. **Missing `CE_IV` column** — Shoonya may not return IV for all strikes; always check `if "CE_IV" in df.columns`
3. **State mutation** — always use `state.x = new_value`, never `state.x.append()` for dicts
4. **Lot size** — must match NSE's current lot size (NIFTY=50 post-2024 revision)
5. **Expiry format** — Shoonya uses `DD-MM-YYYY`; display uses `DD-Mon-YYYY`

## Version History
- v5.3: Modular architecture + IV analytics (ATM IV, IVR, IVP, skew)
- v5.5: Shoonya API backend (replaced broken NSE scraping)
- v5.6: Advanced analytics (GEX, SmartMoney, Flow, Momentum, Dealer, Breakout) + Streamlit + Telegram
