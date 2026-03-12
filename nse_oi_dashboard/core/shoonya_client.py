# ════════════════════════════════════════════════════════════════
#  core/shoonya_client.py  v2 — Symbol Master approach
#  Downloads NFO_symbols.txt.zip from Shoonya (no auth needed),
#  extracts NIFTY/BANKNIFTY option tokens, then batch get_quotes.
#  Bypasses the flaky get_option_chain endpoint entirely.
# ════════════════════════════════════════════════════════════════

import io, os, sys, time, threading, zipfile
from datetime import date, datetime, timedelta
from typing import Optional

import requests
import pandas as pd

# ── Locate and import official ShoonyaApiPy ──────────────────────
def _make_api():
    here  = os.path.dirname(os.path.abspath(__file__))
    proj  = os.path.join(here, "..")
    paths = [
        "/content/ShoonyaApi-py",
        os.path.join(proj, "ShoonyaApi-py"),
        os.path.join(proj, "..", "ShoonyaApi-py"),
        os.path.expanduser("~/ShoonyaApi-py"),
    ]
    for p in paths:
        if os.path.isdir(p):
            if p not in sys.path:
                sys.path.insert(0, p)
            try:
                from api_helper import ShoonyaApiPy
                return ShoonyaApiPy()
            except ImportError:
                pass
    raise ImportError(
        "ShoonyaApi-py not found.\n"
        "Run: git clone https://github.com/Shoonya-Dev/ShoonyaApi-py.git"
    )


# ── Module singletons ─────────────────────────────────────────────
_api:       Optional[object] = None
_lock       = threading.Lock()
_logged_in  = False

# Symbol master cache: DataFrame of NFO option symbols
_sym_master: Optional[pd.DataFrame] = None
_sym_loaded_date: Optional[date]    = None

NFO_MASTER_URL = "https://api.shoonya.com/NFO_symbols.txt.zip"

_INDEX_TOKENS = {
    "NIFTY":     "26000",
    "BANKNIFTY": "26009",
    "FINNIFTY":  "26037",
}


def get_api():
    if _api is None:
        raise RuntimeError("Not logged in — call login() first")
    return _api


# ── Login ─────────────────────────────────────────────────────────
def login() -> bool:
    global _api, _logged_in
    try:
        import credentials as creds
    except ImportError:
        raise FileNotFoundError(
            "credentials.py not found!\n"
            "Copy credentials_template.py → credentials.py"
        )
    import pyotp
    totp = pyotp.TOTP(creds.SHOONYA_TOTP_KEY).now()

    with _lock:
        _api = _make_api()
        ret  = _api.login(
            userid      = creds.SHOONYA_USER_ID,
            password    = creds.SHOONYA_PASSWORD,   # ShoonyaApiPy hashes internally
            twoFA       = totp,
            vendor_code = creds.SHOONYA_VENDOR_CODE,
            api_secret  = creds.SHOONYA_API_SECRET,
            imei        = creds.SHOONYA_IMEI,
        )

    if ret and ret.get("stat") == "Ok":
        _logged_in = True
        print(f"  Shoonya login ✓  user={creds.SHOONYA_USER_ID}  "
              f"token={ret.get('susertoken','')[:12]}…")
        return True
    else:
        err = ret.get("emsg", str(ret)) if ret else "no response"
        raise RuntimeError(
            f"Shoonya login failed: {err}\n"
            "Make sure TOTP_KEY is the secret key, not the 6-digit code."
        )


# ── Spot price ────────────────────────────────────────────────────
def get_spot(symbol: str) -> Optional[float]:
    token = _INDEX_TOKENS.get(symbol.upper())
    if not token:
        return None
    try:
        q = get_api().get_quotes(exchange="NSE", token=token)
        if q and q.get("stat") == "Ok":
            return float(q.get("lp", 0))
    except Exception as e:
        print(f"  get_spot error: {e}")
    return None


# ── VIX ───────────────────────────────────────────────────────────
def get_vix() -> Optional[float]:
    try:
        q = get_api().get_quotes(exchange="NSE", token="26017")
        if q and q.get("stat") == "Ok":
            return float(q.get("lp", 0))
    except Exception as e:
        print(f"  get_vix error: {e}")
    return None


# ── Symbol Master ─────────────────────────────────────────────────
def _load_symbol_master() -> pd.DataFrame:
    """
    Download and cache NFO symbol master from Shoonya.
    Returns DataFrame with columns: Token, TradingSymbol, Instrument,
    Symbol, Expiry, StrikePrice, OptionType, LotSize.
    Re-downloads once per day.
    """
    global _sym_master, _sym_loaded_date
    today = date.today()

    if _sym_master is not None and _sym_loaded_date == today:
        return _sym_master

    print("  Downloading NFO symbol master …", end=" ", flush=True)
    try:
        r = requests.get(NFO_MASTER_URL, timeout=30)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as f:
                df = pd.read_csv(f, header=None)
    except Exception as e:
        print(f"FAILED: {e}")
        return pd.DataFrame()

    # Column layout per Shoonya docs:
    # 0=Exchange, 1=Token, 2=LotSize, 3=Symbol, 4=TradingSymbol,
    # 5=Expiry(DD-Mon-YYYY), 6=Instrument, 7=TickSize,
    # 8=StrikePrice, 9=OptionType, 10=PricePrecision
    df.columns = (list(df.columns[:11]) + list(range(11, len(df.columns))))
    col_map = {
        0: "Exchange", 1: "Token", 2: "LotSize", 3: "Symbol",
        4: "TradingSymbol", 5: "Expiry", 6: "Instrument",
        7: "TickSize", 8: "StrikePrice", 9: "OptionType",
    }
    df.rename(columns=col_map, inplace=True)

    # Keep NFO options only
    df = df[df["Exchange"] == "NFO"].copy()
    df = df[df["Instrument"].isin(["OPTIDX", "OPTSTK"])].copy()
    df["StrikePrice"] = pd.to_numeric(df["StrikePrice"], errors="coerce")
    df["Token"]       = df["Token"].astype(str)

    _sym_master     = df
    _sym_loaded_date = today
    print(f"OK — {len(df):,} NFO option contracts loaded")
    return df


def _nearest_expiry(symbol: str, df: pd.DataFrame) -> str:
    """Return the nearest upcoming expiry date string for symbol."""
    today_str = date.today().isoformat()
    rows = df[df["Symbol"] == symbol].copy()
    if rows.empty:
        return ""
    # Shoonya expiry format varies: "27-Mar-2025" or "27-MAR-2025"
    # Normalise to title-case for strptime then keep original string
    rows["_exp_dt"] = pd.to_datetime(
        rows["Expiry"].str.title(), format="%d-%b-%Y", errors="coerce"
    )
    future = rows[rows["_exp_dt"] >= pd.Timestamp(today_str)]
    if future.empty:
        return ""
    nearest = future["_exp_dt"].min()
    return rows[rows["_exp_dt"] == nearest]["Expiry"].iloc[0]


def _get_option_tokens(symbol: str, expiry: str, atm: float,
                       num_strikes: int) -> pd.DataFrame:
    """
    From symbol master, return token rows for ±num_strikes around ATM.
    Returns DataFrame with Token, TradingSymbol, StrikePrice, OptionType.
    """
    df  = _load_symbol_master()
    sub = df[(df["Symbol"] == symbol) & (df["Expiry"] == expiry)].copy()
    if sub.empty:
        return pd.DataFrame()

    # Sort strikes, keep ±num_strikes around ATM
    strikes = sorted(sub["StrikePrice"].dropna().unique())
    if not strikes:
        return pd.DataFrame()
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - atm))
    lo  = max(0, atm_idx - num_strikes)
    hi  = min(len(strikes), atm_idx + num_strikes + 1)
    sel = strikes[lo:hi]

    return sub[sub["StrikePrice"].isin(sel)][
        ["Token", "TradingSymbol", "StrikePrice", "OptionType", "Expiry", "LotSize"]
    ].copy()


# ── Black-Scholes IV fallback ─────────────────────────────────────
def _bs_iv(ltp: float, spot: float, strike: float, tte: float,
           opt_type: str, r: float = 0.065) -> float:
    """
    Compute implied volatility via bisection on Black-Scholes.
    Used when Shoonya returns iv=0.

    ltp      : option last traded price
    spot     : underlying spot price
    strike   : option strike price
    tte      : time to expiry in years (e.g. 7/365)
    opt_type : "CE" or "PE"
    r        : risk-free rate (Indian 91-day T-bill ~6.5%)

    Returns IV in percent (e.g. 14.5 for 14.5%), or 0.0 on failure.
    """
    import math
    if ltp <= 0 or spot <= 0 or strike <= 0 or tte <= 0:
        return 0.0

    def _bs_price(vol):
        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * vol**2) * tte) / (vol * math.sqrt(tte))
            d2 = d1 - vol * math.sqrt(tte)
            # Standard normal CDF approximation
            def _norm_cdf(x):
                return 0.5 * (1 + math.erf(x / math.sqrt(2)))
            if opt_type == "CE":
                return spot * _norm_cdf(d1) - strike * math.exp(-r * tte) * _norm_cdf(d2)
            else:
                return strike * math.exp(-r * tte) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        except Exception:
            return 0.0

    # Bisection search: IV between 1% and 200%
    lo, hi = 0.01, 2.0
    for _ in range(50):
        mid   = (lo + hi) / 2
        price = _bs_price(mid)
        if price < ltp:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.0001:
            break

    iv = round(((lo + hi) / 2) * 100, 2)
    # Sanity: IV outside 2–150% is noise
    return iv if 2.0 <= iv <= 150.0 else 0.0


def _days_to_expiry(expiry_str: str) -> float:
    """
    Convert expiry string (e.g. '10-MAR-2026') to fraction of year.
    Returns minimum 1/365 to avoid division by zero on expiry day.
    """
    from datetime import date
    try:
        exp = datetime.strptime(expiry_str.title(), "%d-%b-%Y").date()
        days = max(1, (exp - date.today()).days)
        return days / 365.0
    except Exception:
        return 7 / 365.0


# ── Main fetch: option chain ──────────────────────────────────────
def fetch_option_chain(symbol: str, spot: float, num_strikes: int = 20) -> dict:
    """
    Build option chain using symbol master + batch get_quotes.
    Returns NSE-format dict compatible with the rest of the dashboard.
    """
    api    = get_api()
    symbol = symbol.upper()
    step   = 50 if symbol == "NIFTY" else (100 if symbol == "BANKNIFTY" else 50)
    atm    = round(spot / step) * step

    # Load symbol master and find nearest expiry
    master  = _load_symbol_master()
    if master.empty:
        print("  Symbol master unavailable")
        return {}

    expiry = _nearest_expiry(symbol, master)
    if not expiry:
        print(f"  No expiry found for {symbol}")
        return {}

    print(f"  Using expiry: {expiry}")

    # Get option tokens around ATM
    tokens_df = _get_option_tokens(symbol, expiry, atm, num_strikes)
    if tokens_df.empty:
        print(f"  No option tokens found for {symbol} {expiry}")
        return {}

    print(f"  Fetching quotes for {len(tokens_df)} contracts …", end=" ", flush=True)

    # Batch get_quotes for each token
    import re as _re
    quote_map: dict = {}
    for _, row in tokens_df.iterrows():
        token = str(row["Token"])
        try:
            q = api.get_quotes(exchange="NFO", token=token)
            quote_map[token] = q if (q and q.get("stat") == "Ok") else {}
            time.sleep(0.04)   # ~25 req/s — within Shoonya limits
        except Exception as e:
            quote_map[token] = {}

    iv_computed = 0
    tte = _days_to_expiry(expiry)
    print(f"done ({len(quote_map)} quotes, tte={tte*365:.0f}d)")

    # Build NSE-format data list
    from collections import defaultdict
    strike_data: dict = defaultdict(lambda: {"CE": {}, "PE": {}})

    for _, row in tokens_df.iterrows():
        token  = str(row["Token"])
        strike = float(row["StrikePrice"])
        tsym   = str(row["TradingSymbol"])
        # Parse CE/PE from trading symbol — e.g. NIFTY10MAR26P24250 → "PE"
        optype = ""
        for char, label in (("C", "CE"), ("P", "PE")):
            if _re.search(rf"{char}\d", tsym):
                optype = label
                break
        if not optype:
            continue

        q   = quote_map.get(token, {})
        ltp = float(q.get("lp") or 0)

        # Get IV — use Shoonya's value, fall back to Black-Scholes if 0
        shoonya_iv = float(q.get("iv") or 0)
        if shoonya_iv > 0:
            iv_val = shoonya_iv
        elif ltp > 0:
            iv_val = _bs_iv(ltp, spot, strike, tte, optype)
            if iv_val > 0:
                iv_computed += 1
        else:
            iv_val = 0.0

        strike_data[strike][optype] = {
            "openInterest":         int(float(q.get("oi") or 0)),
            "changeinOpenInterest": int(float(q.get("daychngoi") or 0)),
            "lastPrice":            ltp,
            "totalTradedVolume":    int(float(q.get("v") or 0)),
            "impliedVolatility":    iv_val,
        }

    if iv_computed > 0:
        print(f"  IV: {iv_computed} strikes computed via Black-Scholes fallback")

    data_items = [
        {
            "strikePrice": strike,
            "expiryDate":  expiry,
            "CE": strike_data[strike].get("CE", {}),
            "PE": strike_data[strike].get("PE", {}),
        }
        for strike in sorted(strike_data)
    ]

    return {"records": {
        "underlyingValue": spot,
        "timestamp":       datetime.now().strftime("%d-%b-%Y %H:%M:%S"),
        "expiryDates":     [expiry],
        "data":            data_items,
    }}


# ── Expiry helpers ────────────────────────────────────────────────
def _parse_expiry_from_exd(exd: str) -> str:
    """Convert DD-MM-YYYY → DD-Mon-YYYY."""
    if not exd:
        return ""
    try:
        return datetime.strptime(exd, "%d-%m-%Y").strftime("%d-%b-%Y")
    except Exception:
        return exd
