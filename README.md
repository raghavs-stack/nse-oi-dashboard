# NSE Live OI Dashboard  v5.5

Real-time NIFTY/BANKNIFTY options OI dashboard with IV analytics and trade signals.
**Data source: Shoonya (Finvasia) API — free for account holders, no Akamai issues.**

## Features
- Live option chain OI, PCR, Max Pain, Resistance/Support
- ATM IV, IVR, IVP, IV Skew (PUT/CALL heavy detection)
- 4-factor bias voting: PCR + OI Score + Max Pain + RSI/VWAP
- Signal scorer (0–100), trade filter, EOD backtest
- Terminal + Tkinter GUI (3 tabs: Signal | Dual OI | IV Analytics)

## Setup (one-time)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get Shoonya API credentials
1. Open a Shoonya (Finvasia) account at shoonya.com (free)
2. Go to [api.shoonya.com](https://api.shoonya.com) → create an API app
3. Note your **Vendor Code** and **API Secret**

### 3. Set up credentials
```bash
cp credentials_template.py credentials.py
# Edit credentials.py and fill in:
#   SHOONYA_USER_ID     = "FA12345"
#   SHOONYA_PASSWORD    = "yourpassword"
#   SHOONYA_TOTP_KEY    = "your_totp_secret_key"  ← the SECRET, not the 6-digit code
#   SHOONYA_VENDOR_CODE = "from api.shoonya.com"
#   SHOONYA_API_SECRET  = "from api.shoonya.com"
```

**Where to find TOTP_KEY:**
Shoonya app → Settings → Security → Authenticator → "Can't scan? Use key"
Copy the text secret (looks like: `JBSWY3DPEHPK3PXP`). This is your TOTP_KEY.

### 4. Run
```bash
# Terminal mode
python main.py

# GUI mode (local only)
python main.py --gui

# Demo mode (simulated data, no login needed)
python main.py --demo
```

## Architecture
```
nse_oi_dashboard/
├── main.py                  # Entry point
├── config.py                # All settings (symbol, lot size, thresholds)
├── state.py                 # Shared mutable state
├── credentials.py           # YOUR CREDENTIALS (git-ignored)
├── credentials_template.py  # Template to copy
├── requirements.txt
├── core/
│   ├── market_hours.py      # IST timezone, is_market_open()
│   ├── nse_fetcher.py       # Shoonya-backed fetch + DataFrame builder
│   └── shoonya_client.py    # Shoonya login, option chain, spot price
├── signals/
│   ├── indicators.py        # RSI, VWAP, StrategyEngine
│   ├── oi_analytics.py      # PCR, Max Pain, signal scorer
│   └── iv_analytics.py      # ATM IV, IVR, IVP, IV Skew
├── display/
│   ├── terminal.py          # Terminal render with OI + IV charts
│   └── gui.py               # Tkinter GUI (3 tabs)
└── backtest/
    └── eod_backtest.py      # EOD signal backtest
```

## Configuration (`config.py`)
| Setting | Default | Description |
|---|---|---|
| `SYMBOL` | `"NIFTY"` | `"NIFTY"` or `"BANKNIFTY"` |
| `LOT_SIZE` | `50` | Nifty=50, BankNifty=15 |
| `REFRESH_RATE` | `35` | Seconds between fetches |
| `MIN_SIGNAL_SCORE` | `55` | Minimum score to take a trade |
| `MAX_TRADES_PER_DAY` | `3` | Daily trade limit |

## Data from Shoonya API
- **Spot price**: NSE index quotes (token 26000=NIFTY, 26009=BANKNIFTY)
- **Option chain**: `get_option_chain()` + `get_quotes()` per strike
- **VIX**: NSE token 26017
- **IV**: From Shoonya quote `iv` field per option contract
- **OI**: `oi` field, daily change via `daychngoi`
