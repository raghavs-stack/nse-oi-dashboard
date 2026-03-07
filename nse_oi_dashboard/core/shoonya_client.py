# ════════════════════════════════════════════════════════════════
#  core/shoonya_client.py
#  Shoonya (Finvasia) API wrapper for NSE option chain data.
#  Handles login, TOTP, session management, and option chain fetch.
#
#  pip install NorenRestApiPy pyotp
# ════════════════════════════════════════════════════════════════

import hashlib, time, threading
from datetime import datetime
from typing import Optional

# ── Shoonya API class ─────────────────────────────────────────────
class ShoonyaApiPy:
    """Minimal subclass to set Shoonya endpoints."""
    _api = None

    def __init__(self):
        try:
            from NorenRestApiPy.NorenApi import NorenApi
            class _Inner(NorenApi):
                def __init__(self):
                    NorenApi.__init__(
                        self,
                        host="https://api.shoonya.com/NorenWClientTP/",
                        websocket="wss://api.shoonya.com/NorenWSTP/",
                    )
            self._api = _Inner()
        except ImportError:
            raise ImportError(
                "NorenRestApiPy not installed.\n"
                "Run: pip install NorenRestApiPy pyotp"
            )

    # ── Delegate everything to inner api ─────────────────────────
    def __getattr__(self, name):
        return getattr(self._api, name)


# ── Module-level singleton ────────────────────────────────────────
_api:     Optional[ShoonyaApiPy] = None
_lock     = threading.Lock()
_logged_in = False


def get_api() -> ShoonyaApiPy:
    """Return the logged-in API singleton. Call login() first."""
    if _api is None:
        raise RuntimeError("Shoonya not logged in — call login() first")
    return _api


def login() -> bool:
    """
    Login to Shoonya using credentials from credentials.py.
    Generates TOTP automatically from the secret key.
    Returns True on success.
    """
    global _api, _logged_in

    try:
        import credentials as creds
    except ImportError:
        raise FileNotFoundError(
            "credentials.py not found!\n"
            "Copy credentials_template.py → credentials.py and fill in your details."
        )

    try:
        import pyotp
    except ImportError:
        raise ImportError("pyotp not installed. Run: pip install pyotp")

    # Generate current TOTP from secret key
    totp = pyotp.TOTP(creds.SHOONYA_TOTP_KEY)
    current_totp = totp.now()

    # Hash the password (Shoonya expects SHA-256)
    pwd_hash = hashlib.sha256(creds.SHOONYA_PASSWORD.encode()).hexdigest()

    with _lock:
        _api = ShoonyaApiPy()
        ret  = _api.login(
            userid      = creds.SHOONYA_USER_ID,
            password    = pwd_hash,
            twoFA       = current_totp,
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
        print(f"  Shoonya login FAILED: {err}")
        return False


# ── Index spot tokens (NSE exchange) ─────────────────────────────
_INDEX_TOKENS = {
    "NIFTY":     "26000",   # NIFTY 50
    "BANKNIFTY": "26009",   # NIFTY BANK
    "FINNIFTY":  "26037",   # NIFTY FIN SERVICE
}

def get_spot(symbol: str) -> Optional[float]:
    """Get current spot price for index from NSE exchange."""
    token = _INDEX_TOKENS.get(symbol.upper())
    if not token:
        return None
    try:
        api = get_api()
        ret = api.get_quotes(exchange="NSE", token=token)
        if ret and ret.get("stat") == "Ok":
            return float(ret.get("lp", 0))
    except Exception as e:
        print(f"  get_spot({symbol}) error: {e}")
    return None


def _parse_expiry(tsym: str, symbol: str) -> str:
    """
    Parse expiry date string from Shoonya tsym.
    Format: NIFTY25MAR24000CE  →  "27-Mar-2025"
    Returns DD-Mon-YYYY string matching NSE format used by build_df().
    """
    # Strip symbol prefix
    rest = tsym[len(symbol):]           # "25MAR24000CE"
    yy   = rest[:2]                     # "25"
    mon  = rest[2:5]                    # "MAR"
    year = f"20{yy}"                    # "2025"

    mon_map = {
        "JAN":"01","FEB":"02","MAR":"03","APR":"04",
        "MAY":"05","JUN":"06","JUL":"07","AUG":"08",
        "SEP":"09","OCT":"10","NOV":"11","DEC":"12"
    }
    mon_num = mon_map.get(mon.upper(), "01")

    # Find last Thursday of the month as expiry (NSE standard)
    # But simpler: use what Shoonya returns in 'exd' field if available
    # This fallback uses 1st of month as placeholder — overridden by exd field
    return f"01-{mon[:1].upper()}{mon[1:].lower()}-{year}"


def _parse_expiry_from_exd(exd: str) -> str:
    """
    Convert Shoonya exd field (DD-MM-YYYY) to NSE format (DD-Mon-YYYY).
    e.g. "27-03-2025" → "27-Mar-2025"
    """
    if not exd:
        return ""
    try:
        dt = datetime.strptime(exd, "%d-%m-%Y")
        return dt.strftime("%d-%b-%Y")   # "27-Mar-2025"
    except Exception:
        return exd


def fetch_option_chain(symbol: str, spot: float, num_strikes: int = 20) -> dict:
    """
    Fetch full option chain from Shoonya and normalise to NSE format.

    Returns dict matching NSE option-chain-indices structure:
    {
        "records": {
            "underlyingValue": 24865.0,
            "expiryDates": ["27-Mar-2025", ...],
            "timestamp": "04-Mar-2025 10:15:00",
            "data": [
                {
                    "strikePrice": 24000.0,
                    "expiryDate":  "27-Mar-2025",
                    "CE": {"openInterest":..., "changeinOpenInterest":...,
                           "lastPrice":..., "totalTradedVolume":...,
                           "impliedVolatility":...},
                    "PE": {...}
                }, ...
            ]
        }
    }
    """
    api    = get_api()
    symbol = symbol.upper()

    # Step 1: get option chain symbol list around ATM strike
    step    = 50 if symbol == "NIFTY" else 100
    atm     = round(spot / step) * step

    ret = api.get_option_chain(
        exchange       = "NFO",
        tradingsymbol  = symbol,
        strikeprice    = str(int(atm)),
        count          = str(num_strikes),
    )

    if not ret or ret.get("stat") != "Ok":
        err = ret.get("emsg", str(ret)) if ret else "no response"
        print(f"  get_option_chain failed: {err}")
        return {}

    values = ret.get("values", [])
    if not values:
        print("  get_option_chain: empty values")
        return {}

    # Step 2: group by (strike, expiry) → {CE: token, PE: token}
    # Build a map: strike → {expiry, ce_token, pe_token}
    strike_map: dict = {}
    expiry_set: set  = set()

    for v in values:
        tsym   = v.get("tsym", "")
        optt   = v.get("optt", "")        # "CE" or "PE"
        token  = v.get("token", "")
        strprc = float(v.get("strprc", 0))
        exd    = _parse_expiry_from_exd(v.get("exd", ""))

        if not exd:
            # Fallback: parse from tsym
            exd = _parse_expiry(tsym, symbol)

        expiry_set.add(exd)
        key = (strprc, exd)
        if key not in strike_map:
            strike_map[key] = {"strike": strprc, "expiry": exd,
                               "ce_token": None, "pe_token": None}
        if optt == "CE":
            strike_map[key]["ce_token"] = token
        elif optt == "PE":
            strike_map[key]["pe_token"] = token

    # Step 3: fetch quotes for every token
    # Build token→quote map in one pass to avoid duplicate calls
    all_tokens = {}
    for info in strike_map.values():
        if info["ce_token"]: all_tokens[info["ce_token"]] = None
        if info["pe_token"]: all_tokens[info["pe_token"]] = None

    for token in all_tokens:
        try:
            q = api.get_quotes(exchange="NFO", token=token)
            all_tokens[token] = q if (q and q.get("stat") == "Ok") else {}
            time.sleep(0.05)   # small delay to avoid rate limits
        except Exception as e:
            print(f"  get_quotes token={token} error: {e}")
            all_tokens[token] = {}

    # Step 4: assemble NSE-format data list
    data_items = []
    for (strike, expiry), info in sorted(strike_map.items()):
        ce_q = all_tokens.get(info["ce_token"], {})
        pe_q = all_tokens.get(info["pe_token"], {})

        def _int(q, k):   return int(float(q.get(k, 0) or 0))
        def _float(q, k): return float(q.get(k, 0) or 0)

        data_items.append({
            "strikePrice": strike,
            "expiryDate":  expiry,
            "CE": {
                "openInterest":        _int(ce_q,   "oi"),
                "changeinOpenInterest": _int(ce_q,   "daychngoi"),
                "lastPrice":           _float(ce_q,  "lp"),
                "totalTradedVolume":   _int(ce_q,   "v"),
                "impliedVolatility":   _float(ce_q,  "iv"),
            },
            "PE": {
                "openInterest":        _int(pe_q,   "oi"),
                "changeinOpenInterest": _int(pe_q,   "daychngoi"),
                "lastPrice":           _float(pe_q,  "lp"),
                "totalTradedVolume":   _int(pe_q,   "v"),
                "impliedVolatility":   _float(pe_q,  "iv"),
            },
        })

    expiry_list = sorted(expiry_set, key=lambda d: datetime.strptime(d, "%d-%b-%Y"))

    return {"records": {
        "underlyingValue": spot,
        "timestamp":       datetime.now().strftime("%d-%b-%Y %H:%M:%S"),
        "expiryDates":     expiry_list,
        "data":            data_items,
    }}
