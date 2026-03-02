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
# Two separate header sets — browser headers for page visits,
# API headers for JSON endpoints (different Accept header matters)
_BROWSER_HEADERS = {
    "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
NSE_HEADERS = {
    "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/option-chain",
    "X-Requested-With": "XMLHttpRequest",
}

_global_session: requests.Session = requests.Session()
_cookie_lock = threading.Lock()


def _visit(url: str, referer: str = "https://www.nseindia.com/", delay: float = 2.0, label: str = ""):
    """Hit a browser URL to accumulate cookies in the session jar."""
    hdrs = {**_BROWSER_HEADERS, "Referer": referer}
    try:
        r = _global_session.get(url, headers=hdrs, timeout=15)
        if label:
            ck = list(r.cookies.keys())
            all_ck = list(_global_session.cookies.keys())
            print(f"  [{label}] HTTP {r.status_code}  new={ck}  jar={all_ck}")
        time.sleep(delay)
        return r
    except requests.RequestException as e:
        print(f"  [{label}] failed: {e}")
        return None


def create_session() -> requests.Session:
    """
    NSE session warmup that reliably gets nseappid + nsit + Akamai cookies.

    Key insight from diagnosis:
    - Homepage returns 403 from Colab IPs → skip it
    - Market data page gives: nsit, _abck, bm_sz  (Akamai cookies)
    - Option-chain page gives: nseappid  (required for API calls)
    - Order matters: option-chain MUST be hit on a session that already has
      Akamai cookies, otherwise nseappid is not set

    Correct sequence:
      1. market-data page  → nsit + Akamai tokens
      2. option-chain page → nseappid
      3. marketStatus API  → validates session
      4. allIndices API    → confirms spots are reachable
    """
    global _global_session
    with _cookie_lock:
        _global_session = requests.Session()
        _global_session.headers.update(_BROWSER_HEADERS)

    print("  Warming up NSE session...")

    # Step 1: market data page → nsit, _abck, bm_sz
    _visit("https://www.nseindia.com/market-data/live-equity-market",
           referer="https://www.google.com/", delay=2.5, label="market-data")

    # Step 2: option-chain page → nseappid  (must come AFTER Akamai cookies)
    _visit("https://www.nseindia.com/option-chain",
           referer="https://www.nseindia.com/market-data/live-equity-market",
           delay=2.5, label="option-chain")

    # ── KEY FIX: nseappid is set by JavaScript (requests can't run JS).
    # NSE's JS sets nseappid = nsit value. Set it manually in the jar.
    nsit = _global_session.cookies.get("nsit", "")
    if nsit:
        _global_session.cookies.set("nseappid", nsit, domain=".nseindia.com")
        print(f"  nseappid set manually from nsit ✓  (nsit={nsit[:12]}…)")
    else:
        print("  ⚠ nsit missing — nseappid cannot be set. Option chain may fail.")

    time.sleep(1.0)

    # Final validate
    try:
        r = _global_session.get(
            "https://www.nseindia.com/api/allIndices",
            headers=NSE_HEADERS, timeout=12)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            d = json.loads(r.text)
            spots = [(x["index"], x["last"]) for x in d.get("data", [])
                     if x.get("index") in ("NIFTY 50", "NIFTY BANK")]
            spot_str = "  ".join(f"{s[0]}={s[1]:,.0f}" for s in spots) if spots else "OK"
            jar_keys = list(_global_session.cookies.keys())
            has_nseappid = "nseappid" in jar_keys
            print(f"  NSE session ready ✓  {spot_str}")
            print(f"  Cookies: {jar_keys}  |  nseappid={'✓' if has_nseappid else '✗ MISSING'}")
            if not has_nseappid:
                print("  ⚠ nseappid missing — option chain may return {}. "
                      "Retrying warmup in 5s...")
                time.sleep(5)
                return create_session()   # one automatic retry
        else:
            print(f"  NSE warmup: allIndices HTTP {r.status_code} — proceeding anyway")
    except Exception as e:
        print(f"  NSE warmup: validate skipped ({e})")

    return _global_session


def _get_data(url: str) -> str:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = _global_session.get(url, headers=NSE_HEADERS, timeout=15)

            if r.status_code == 200:
                text = r.text.strip()
                if not text or text.startswith("<") or text in ("{}", "null"):
                    print(f"Invalid JSON attempt {attempt}")
                    time.sleep(4 * attempt)
                    continue
                return text

            elif r.status_code in (401, 403):
                print(f"HTTP {r.status_code} — rebuilding session")
                create_session()
                time.sleep(6 * attempt)

            elif r.status_code == 429:
                print("Rate limited — sleeping 60s")
                time.sleep(60)

            else:
                print(f"HTTP {r.status_code}")
                time.sleep(4 * attempt)

        except requests.RequestException as e:
            print(f"Network error: {e}")
            time.sleep(4 * attempt)

    return ""


def fetch_chain(session, symbol: str) -> dict | None:
    """
    Fetch option chain for symbol and normalize into a consistent structure.
    Uses session cookie jar (no manual cookie dict needed).
    Returns None on any failure — callers use `if not data`.
    """
    url  = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    text = _get_data(url)
    if not text:
        return None

    stripped = text.strip()
    if stripped.startswith("<"):
        print("NSE returned HTML — session needs rebuild")
        create_session()
        return None

    try:
        raw = json.loads(text)
    except Exception as e:
        print(f"JSON parse error: {e}  (response[:100]: {text[:100]!r})")
        return None

    if not isinstance(raw, dict) or not raw:
        print(f"NSE returned empty/bad response — Cloudflare block likely")
        return None

    # Shape A: {"records": {"data": [...], "underlyingValue": ...}}
    if "records" in raw and isinstance(raw["records"], dict):
        rec = raw["records"]
        if "data" in rec and "underlyingValue" in rec:
            return raw
        # Patch from "filtered" if records is incomplete
        if "filtered" in raw and isinstance(raw["filtered"], dict):
            filt = raw["filtered"]
            rec.setdefault("data",            filt.get("data", []))
            rec.setdefault("expiryDates",     filt.get("expiryDates", []))
            rec.setdefault("underlyingValue", filt.get("underlyingValue", 0))
            if rec.get("data") and rec.get("underlyingValue"):
                return raw

    # Shape B: {"filtered": {"data": [...], "underlyingValue": ...}}
    if "filtered" in raw and isinstance(raw["filtered"], dict):
        filt = raw["filtered"]
        if filt.get("data") and filt.get("underlyingValue"):
            return {"records": {
                "data":            filt["data"],
                "expiryDates":     filt.get("expiryDates", []),
                "underlyingValue": filt["underlyingValue"],
                "timestamp":       filt.get("timestamp", ""),
            }}

    # Shape C: {"data": [...], "underlyingValue": ...}
    if "data" in raw and "underlyingValue" in raw:
        return {"records": {
            "data":            raw["data"],
            "expiryDates":     raw.get("expiryDates", []),
            "underlyingValue": raw["underlyingValue"],
            "timestamp":       raw.get("timestamp", ""),
        }}

    print(f"NSE unknown response. Keys: {list(raw.keys())}")
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
    """Uses fetch_chain normalization so shape A/B/C are all handled."""
    # Reuse fetch_chain's normalization by calling _get_data + manual parse
    text = _get_data(url)
    if not text:
        return [], 0, 0, ""
    try:
        raw = json.loads(text)
        # Normalize using same logic as fetch_chain
        if "records" in raw and "data" in raw["records"]:
            rec = raw["records"]
        elif "filtered" in raw and raw["filtered"].get("data"):
            rec = raw["filtered"]
        elif "data" in raw and "underlyingValue" in raw:
            rec = raw
        else:
            return [], 0, 0, ""

        expiry_dates = rec.get("expiryDates", [])
        if not expiry_dates:
            return [], 0, 0, ""
        curr_expiry  = expiry_dates[0]
        start_strike = nearest - (step * num)
        end_strike   = nearest + (step * num)

        max_oi_ce = max_oi_pe = 0
        max_oi_ce_strike = max_oi_pe_strike = 0
        oi_data_list = []

        for item in rec["data"]:
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
    except Exception as e:
        print(f"_get_highest_oi_strikes error: {e}")
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
