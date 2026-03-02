# ════════════════════════════════════════════════════════════════
#  core/nse_fetcher.py  v5.3.2
#  Thread-safe NSE session + option chain parser.
#  Cookie pattern ported from haripm2211/nse/nse_data.py.
#  build_df() now captures IV columns (v5.3) for iv_analytics.
#
#  SENTINEL — DO NOT REMOVE (used by colab_runner.py to detect stale files)
NSE_FETCHER_VERSION = "5.3.2"
# ════════════════════════════════════════════════════════════════

import json, math, random, threading, time
import requests
import pandas as pd

from config import MAX_RETRIES, REFRESH_RATE
from core.market_hours import now_ist

# ── Strike helpers (haripm2211 nse/nse_data.py) ──────────────────
def nearest_strike_nf(x: float) -> int:
    return int(math.ceil(float(x) / 50) * 50)

def nearest_strike_bnf(x: float) -> int:
    return int(math.ceil(float(x) / 100) * 100)

def nearest_strike(x: float, symbol: str) -> int:
    return nearest_strike_bnf(x) if symbol == "BANKNIFTY" else nearest_strike_nf(x)

def strike_step(symbol: str) -> int:
    return 100 if symbol == "BANKNIFTY" else 50


# ── NSE session ───────────────────────────────────────────────────
NSE_HEADERS = {
    "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/option-chain",
}

_global_session = requests.Session()
_global_cookies: dict = {}
_cookie_lock    = threading.Lock()


def _set_cookie(session, cookies_dict: dict):
    """Thread-safe cookie refresh (haripm2211 pattern)."""
    with _cookie_lock:
        try:
            r = session.get(
                "https://www.nseindia.com/option-chain",
                headers=NSE_HEADERS, timeout=8)
            cookies_dict.update(r.cookies)
        except requests.RequestException:
            pass


def create_session() -> requests.Session:
    """
    Colab-compatible NSE session warmup.

    Cloudflare requires a realistic browser navigation sequence:
      1. Hit homepage (establishes initial CF cookie)
      2. Sleep — simulate human reading time
      3. Navigate to option-chain page (sets __cfduid + nsit + nseappid)
      4. Hit market-status (warms API token)
      5. Validate with allIndices call

    Colab cloud IPs are flagged by Cloudflare; longer delays help.
    Session cookies expire every ~5 minutes on NSE — rebuild every 10 cycles.
    """
    global _global_session, _global_cookies
    _global_session = requests.Session()
    _global_session.headers.update(NSE_HEADERS)
    _global_cookies = {}

    steps = [
        ("https://www.nseindia.com/",                              2.5, "homepage"),
        ("https://www.nseindia.com/market-data/live-equity-market", 1.5, "market page"),
        ("https://www.nseindia.com/option-chain",                   2.0, "option-chain"),
        ("https://www.nseindia.com/api/marketStatus",               1.0, "marketStatus"),
    ]

    for url, delay, label in steps:
        try:
            r = _global_session.get(url, headers=NSE_HEADERS, timeout=15)
            _global_cookies.update(r.cookies)
            time.sleep(delay)
        except requests.RequestException as e:
            print(f"  Warmup step '{label}' failed: {e}")

    # Final validate
    try:
        r = _global_session.get(
            "https://www.nseindia.com/api/allIndices",
            headers=NSE_HEADERS, cookies=_global_cookies, timeout=12)
        _global_cookies.update(r.cookies)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            import json as _j
            rd = _j.loads(r.text)
            spots = [x["last"] for x in rd.get("data",[]) if x.get("index") in ("NIFTY 50","NIFTY BANK")]
            spot_str = "  ".join(f"{x:,.0f}" for x in spots) if spots else "OK"
            print(f"NSE session ready ✓  Spots: {spot_str}")
        else:
            print(f"NSE session warmup done (validate HTTP {r.status_code}) — proceeding")
    except Exception as e:
        print(f"NSE session warmup complete (validate skipped: {e})")

    return _global_session


def _get_data(url: str) -> str:
    """
    Fetch with cookie refresh before every attempt.
    NSE/Cloudflare on Colab IPs invalidates tokens quickly —
    we refresh cookies before each call AND absorb any new cookies from responses.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        _set_cookie(_global_session, _global_cookies)   # always refresh first
        try:
            r = _global_session.get(
                url, headers=NSE_HEADERS,
                cookies=_global_cookies, timeout=12)
            _global_cookies.update(r.cookies)           # absorb any new tokens

            if r.status_code == 200:
                text = r.text.strip()
                if text and not text.startswith("<"):
                    return r.text
                if text.startswith("<"):
                    print(f"NSE returned HTML on attempt {attempt} — Cloudflare block")
                    time.sleep(5 * attempt)
                    continue
                # Empty body
                print(f"NSE returned empty body on attempt {attempt}")
                time.sleep(4 * attempt)
            elif r.status_code in (401, 403):
                print(f"HTTP {r.status_code} on attempt {attempt} — rebuilding session")
                create_session()
                time.sleep(6 * attempt)
            elif r.status_code == 429:
                print(f"HTTP 429 Rate Limited on attempt {attempt} — sleeping 30s")
                time.sleep(30)
            else:
                print(f"HTTP {r.status_code} on attempt {attempt}")
                time.sleep(3 * attempt)
        except requests.Timeout:
            print(f"Timeout attempt {attempt}")
            time.sleep(4 * attempt)
        except requests.RequestException as e:
            print(f"Network error: {e}")
            time.sleep(4 * attempt)
    return ""


def fetch_chain(session, symbol: str) -> dict | None:
    """
    Fetch option chain for symbol and normalize into a consistent structure.

    NSE API has returned data in 3 different shapes over the years:
      Shape A (most common):  {"records": {"data":[...], "expiryDates":[...], "underlyingValue":...}}
      Shape B (some periods): {"filtered": {"data":[...], ...}, "records": {...}}
      Shape C (rare / new):   {"data": [...], "expiryDates": [...], "underlyingValue": ...}

    This function always returns Shape A regardless of which shape NSE sends,
    so process_cycle() can safely use data["records"]["data"] everywhere.
    Returns None on ANY failure so callers can use `if not data`.
    """
    url  = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    text = _get_data(url)
    if not text:
        return None

    # If we got HTML (session expired / Cloudflare redirect), bail early
    stripped = text.strip()
    if stripped.startswith("<"):
        print("NSE returned HTML — Cloudflare blocked or session expired. Rebuilding session...")
        create_session()
        return None

    try:
        raw = json.loads(text)
    except Exception as e:
        print(f"JSON parse error: {e}  (response[:100]: {text[:100]!r})")
        return None

    # {} or non-dict = Cloudflare rate-limit or error response
    if not isinstance(raw, dict) or not raw:
        print(f"NSE returned empty/non-dict response ({type(raw).__name__}). "
              f"Cloudflare block likely. Rebuilding session in 10s...")
        time.sleep(10)
        create_session()
        return None

    # ── Shape A: already has "records" with all fields ────────────
    if "records" in raw and isinstance(raw["records"], dict):
        rec = raw["records"]
        # Validate it has the minimum we need
        if "data" in rec and "underlyingValue" in rec:
            return raw           # perfect — use as-is

        # "records" exists but is incomplete — try to patch from "filtered"
        if "filtered" in raw and isinstance(raw["filtered"], dict):
            filt = raw["filtered"]
            rec.setdefault("data",            filt.get("data", []))
            rec.setdefault("expiryDates",     filt.get("expiryDates", []))
            rec.setdefault("underlyingValue", filt.get("underlyingValue", 0))
            if rec.get("data") and rec.get("underlyingValue"):
                return raw

    # ── Shape B: only "filtered" (no usable "records") ────────────
    if "filtered" in raw and isinstance(raw["filtered"], dict):
        filt = raw["filtered"]
        if filt.get("data") and filt.get("underlyingValue"):
            print("NSE Shape B: using 'filtered' block")
            return {"records": {
                "data":            filt["data"],
                "expiryDates":     filt.get("expiryDates", []),
                "underlyingValue": filt["underlyingValue"],
                "timestamp":       filt.get("timestamp", ""),
            }}

    # ── Shape C: flat structure at root level ─────────────────────
    if "data" in raw and "underlyingValue" in raw:
        print("NSE Shape C: flat root structure")
        return {"records": {
            "data":            raw["data"],
            "expiryDates":     raw.get("expiryDates", []),
            "underlyingValue": raw["underlyingValue"],
            "timestamp":       raw.get("timestamp", ""),
        }}

    # ── Unknown shape — print keys to help debug ──────────────────
    print(f"NSE unknown response shape. Top-level keys: {list(raw.keys())}")
    for k, v in raw.items():
        if isinstance(v, dict):
            print(f"  '{k}' sub-keys: {list(v.keys())[:10]}")
        elif isinstance(v, list):
            print(f"  '{k}' list len={len(v)}")
    return None


def fetch_vix() -> str:
    """India VIX via yfinance — fallback 'N/A'."""
    try:
        import yfinance as yf
        v = yf.Ticker("^INDIAVIX").history(period="1d")
        if not v.empty:
            return str(round(v["Close"].iloc[-1], 2))
    except Exception:
        pass
    return "N/A"


# ── Option chain → DataFrame ──────────────────────────────────────
def build_df(data_items: list, expiry: str) -> pd.DataFrame:
    """
    Parse NSE option chain data into a tidy DataFrame.
    v5.3 adds CE_IV / PE_IV (impliedVolatility) columns used by iv_analytics.
    """
    rows = []
    for item in data_items:
        if item.get("expiryDate") != expiry:
            continue
        ce = item.get("CE", {})
        pe = item.get("PE", {})
        rows.append({
            "Strike":  item["strikePrice"],
            "CE_OI":   ce.get("openInterest", 0),
            "PE_OI":   pe.get("openInterest", 0),
            "CE_Chg":  ce.get("changeinOpenInterest", 0),
            "PE_Chg":  pe.get("changeinOpenInterest", 0),
            "CE_LTP":  ce.get("lastPrice", 0),
            "PE_LTP":  pe.get("lastPrice", 0),
            "CE_Vol":  ce.get("totalTradedVolume", 0),
            "PE_Vol":  pe.get("totalTradedVolume", 0),
            "CE_IV":   ce.get("impliedVolatility", 0),   # v5.3 NEW
            "PE_IV":   pe.get("impliedVolatility", 0),   # v5.3 NEW
        })
    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)


# ── Dual-index fetcher (haripm2211 fetch_oi_data_for_ui port) ────
def _get_highest_oi_strikes(num: int, step: int, nearest: int, url: str):
    text = _get_data(url)
    if not text:
        return [], 0, 0, ""
    try:
        data = json.loads(text)
        expiry_dates = data["records"].get("expiryDates", [])
        if not expiry_dates:
            return [], 0, 0, ""
        curr_expiry  = expiry_dates[0]
        start_strike = nearest - (step * num)
        end_strike   = nearest + (step * num)

        max_oi_ce = max_oi_pe = 0
        max_oi_ce_strike = max_oi_pe_strike = 0
        oi_data_list = []

        for item in data["records"]["data"]:
            if item.get("expiryDate") != curr_expiry:
                continue
            sp = item["strikePrice"]
            if not (start_strike <= sp <= end_strike):
                continue
            ce_oi = item.get("CE", {}).get("openInterest", 0)
            pe_oi = item.get("PE", {}).get("openInterest", 0)
            oi_data_list.append({"strike": sp, "ce_oi": ce_oi, "pe_oi": pe_oi})
            if ce_oi > max_oi_ce:
                max_oi_ce, max_oi_ce_strike = ce_oi, sp
            if pe_oi > max_oi_pe:
                max_oi_pe, max_oi_pe_strike = pe_oi, sp

        oi_data_list.sort(key=lambda x: x["strike"])
        return oi_data_list, int(max_oi_ce_strike), int(max_oi_pe_strike), curr_expiry
    except Exception:
        return [], 0, 0, ""


def fetch_oi_data_dual() -> dict | None:
    """Returns dict keyed 'NIFTY'/'BANKNIFTY' with ltp, oi_data, resistance, support, expiry."""
    text = _get_data("https://www.nseindia.com/api/allIndices")
    if not text:
        return None
    try:
        data = json.loads(text)
        nf_ul = bnf_ul = 0
        for idx in data["data"]:
            if idx["index"] == "NIFTY 50":
                nf_ul = idx["last"]
            if idx["index"] == "NIFTY BANK":
                bnf_ul = idx["last"]
        if nf_ul == 0 and bnf_ul == 0:
            return None

        nf_nearest  = nearest_strike_nf(nf_ul)
        bnf_nearest = nearest_strike_bnf(bnf_ul)

        nf_oi,  nf_res,  nf_sup,  nf_exp = _get_highest_oi_strikes(
            10, 50, nf_nearest,
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY")
        bnf_oi, bnf_res, bnf_sup, bnf_exp = _get_highest_oi_strikes(
            10, 100, bnf_nearest,
            "https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY")

        return {
            "NIFTY":    {"ltp": nf_ul,  "nearest_strike": nf_nearest,
                         "atm": nf_nearest,  "oi_data": nf_oi,
                         "max_resistance": nf_res, "max_support": nf_sup,
                         "expiry": nf_exp},
            "BANKNIFTY": {"ltp": bnf_ul, "nearest_strike": bnf_nearest,
                          "atm": bnf_nearest, "oi_data": bnf_oi,
                          "max_resistance": bnf_res, "max_support": bnf_sup,
                          "expiry": bnf_exp},
        }
    except Exception:
        return None


# ── Demo data (simulated option chain with IV) ────────────────────
def demo_data(symbol: str, cycle: int) -> dict:
    """
    Realistic demo option chain used when markets are closed.
    v5.3: adds CE_IV / PE_IV with a simulated volatility smile.
    """
    step  = 50 if symbol == "NIFTY" else 100
    base  = 22_450 if symbol == "NIFTY" else 48_500
    atm_iv_base = 11.5 if symbol == "NIFTY" else 13.0   # NIFTY ATM IV ~11-12%

    spot   = base + math.sin(cycle * 0.3) * 90 + random.uniform(-20, 20)
    atm    = round(spot / step) * step
    expiry = "06-Mar-2026"

    # Simulate daily drift in ATM IV
    atm_iv = atm_iv_base + math.sin(cycle * 0.15) * 1.2 + random.uniform(-0.3, 0.3)
    atm_iv = max(8.0, min(25.0, atm_iv))

    items = []
    for i in range(-10, 11):
        s    = atm + i * step
        # Volatility smile: puts are steeper (negative skew typical of equity indices)
        # CE side: slight smile  PE side: pronounced skew (fear premium)
        if i <= 0:   # PE side — OTM puts have higher IV (put skew)
            iv_ce = atm_iv + 0.05 * i ** 2          # slight CE smile
            iv_pe = atm_iv + 0.25 * i ** 2 + 0.15 * abs(i)  # steep put skew
        else:        # CE side — OTM calls have modest smile
            iv_ce = atm_iv + 0.08 * i ** 2 + 0.05 * i
            iv_pe = atm_iv + 0.04 * i ** 2

        iv_ce = max(5.0, round(iv_ce + random.uniform(-0.2, 0.2), 2))
        iv_pe = max(5.0, round(iv_pe + random.uniform(-0.2, 0.2), 2))

        ce_b   = max(1000, int(800_000 * math.exp(-0.08 * (i + 2) ** 2)))
        pe_b   = max(1000, int(900_000 * math.exp(-0.08 * (i - 2) ** 2)))
        ce_oi  = ce_b + random.randint(-5000, 25000) * (cycle % 3 + 1)
        pe_oi  = pe_b + random.randint(-5000, 25000) * (cycle % 3 + 1)
        ce_chg = max(0, random.randint(500, 35000) + (15000 if i > 1 else 0))
        pe_chg = max(0, random.randint(500, 35000) + (15000 if i < -1 else 0))
        if cycle % 4 == 0 and i == 3:
            ce_oi += 130_000; ce_chg += 130_000
        ce_ltp = max(1.0, round((200 - max(0, s - atm)) * 0.9 + random.uniform(-3, 3), 1))
        pe_ltp = max(1.0, round((200 - max(0, atm - s)) * 0.9 + random.uniform(-3, 3), 1))

        items.append({
            "strikePrice": float(s), "expiryDate": expiry,
            "CE": {"openInterest": ce_oi, "changeinOpenInterest": ce_chg,
                   "lastPrice": ce_ltp, "totalTradedVolume": random.randint(20000, 80000),
                   "impliedVolatility": iv_ce},     # v5.3
            "PE": {"openInterest": pe_oi, "changeinOpenInterest": pe_chg,
                   "lastPrice": pe_ltp, "totalTradedVolume": random.randint(20000, 80000),
                   "impliedVolatility": iv_pe},     # v5.3
        })

    return {"records": {
        "underlyingValue": round(spot, 2),
        "timestamp":       now_ist().strftime("%d-%b-%Y %H:%M:%S"),
        "expiryDates":     [expiry, "13-Mar-2026", "27-Mar-2026"],
        "data":            items,
    }}
