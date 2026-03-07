# ════════════════════════════════════════════════════════════════
#  core/nse_fetcher.py  v5.5  (Shoonya backend)
#  All NSE data comes from Shoonya (Finvasia) API — free, official,
#  no Akamai/cookie/TLS issues.
#
#  SENTINEL
NSE_FETCHER_VERSION = "5.5"
# ════════════════════════════════════════════════════════════════

import json, math, random, time
import pandas as pd

from config import MAX_RETRIES
from core.market_hours import now_ist

# ── Strike helpers ────────────────────────────────────────────────
def nearest_strike_nf(x):   return int(math.ceil(float(x) / 50)  * 50)
def nearest_strike_bnf(x):  return int(math.ceil(float(x) / 100) * 100)
def nearest_strike(x, sym): return nearest_strike_bnf(x) if sym == "BANKNIFTY" else nearest_strike_nf(x)
def strike_step(sym):        return 100 if sym == "BANKNIFTY" else 50


# ── Session (Shoonya login) ───────────────────────────────────────
def create_session():
    """Login to Shoonya. Called once at startup."""
    from core.shoonya_client import login
    ok = login()
    if not ok:
        raise RuntimeError(
            "Shoonya login failed. Check credentials.py and try again.\n"
            "Make sure TOTP_KEY is the secret key, not the 6-digit code."
        )


# ── Option chain fetch ────────────────────────────────────────────
def fetch_chain(session, symbol: str) -> dict | None:
    from core.shoonya_client import get_spot, fetch_option_chain
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            spot = get_spot(symbol)
            if not spot:
                print(f"  fetch_chain: no spot price attempt {attempt}")
                time.sleep(3 * attempt); continue
            data = fetch_option_chain(symbol, spot, num_strikes=20)
            if not data:
                print(f"  fetch_chain: empty chain attempt {attempt}")
                time.sleep(3 * attempt); continue
            rec = data.get("records", {})
            if rec.get("data") and rec.get("underlyingValue"):
                return data
            print(f"  fetch_chain: incomplete data attempt {attempt}")
            time.sleep(3 * attempt)
        except Exception as e:
            print(f"  fetch_chain error attempt {attempt}: {e}")
            time.sleep(4 * attempt)
    return None


# ── VIX ───────────────────────────────────────────────────────────
def fetch_vix() -> str:
    """India VIX — Shoonya token 26017, fallback yfinance."""
    try:
        from core.shoonya_client import get_api
        ret = get_api().get_quotes(exchange="NSE", token="26017")
        if ret and ret.get("stat") == "Ok" and ret.get("lp"):
            return str(round(float(ret["lp"]), 2))
    except Exception:
        pass
    try:
        import yfinance as yf
        v = yf.Ticker("^INDIAVIX").history(period="1d")
        if not v.empty:
            return str(round(v["Close"].iloc[-1], 2))
    except Exception:
        pass
    return "N/A"


# ── DataFrame builder ─────────────────────────────────────────────
def build_df(data_items: list, expiry: str) -> pd.DataFrame:
    rows = []
    for item in data_items:
        if item.get("expiryDate") != expiry:
            continue
        ce = item.get("CE", {})
        pe = item.get("PE", {})
        rows.append({
            "Strike": item["strikePrice"],
            "CE_OI":  ce.get("openInterest", 0),
            "PE_OI":  pe.get("openInterest", 0),
            "CE_Chg": ce.get("changeinOpenInterest", 0),
            "PE_Chg": pe.get("changeinOpenInterest", 0),
            "CE_LTP": ce.get("lastPrice", 0),
            "PE_LTP": pe.get("lastPrice", 0),
            "CE_Vol": ce.get("totalTradedVolume", 0),
            "PE_Vol": pe.get("totalTradedVolume", 0),
            "CE_IV":  ce.get("impliedVolatility", 0),
            "PE_IV":  pe.get("impliedVolatility", 0),
        })
    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)


# ── Dual-index OI (GUI Dual OI tab) ──────────────────────────────
def _get_highest_oi_strikes(symbol, num, step, nearest):
    data = fetch_chain(None, symbol)
    if not data:
        return [], 0, 0, ""
    try:
        rec = data["records"]
        expiry_dates = rec.get("expiryDates", [])
        if not expiry_dates:
            return [], 0, 0, ""
        curr_expiry  = expiry_dates[0]
        start_strike = nearest - step * num
        end_strike   = nearest + step * num
        max_ce = max_pe = 0
        max_ce_s = max_pe_s = 0
        oi_list = []
        for item in rec["data"]:
            if item.get("expiryDate") != curr_expiry:
                continue
            sp = item["strikePrice"]
            if not (start_strike <= sp <= end_strike):
                continue
            ce_oi = item.get("CE", {}).get("openInterest", 0)
            pe_oi = item.get("PE", {}).get("openInterest", 0)
            oi_list.append({"strike": sp, "ce_oi": ce_oi, "pe_oi": pe_oi})
            if ce_oi > max_ce: max_ce, max_ce_s = ce_oi, sp
            if pe_oi > max_pe: max_pe, max_pe_s = pe_oi, sp
        oi_list.sort(key=lambda x: x["strike"])
        return oi_list, int(max_ce_s), int(max_pe_s), curr_expiry
    except Exception as e:
        print(f"  _get_highest_oi_strikes error: {e}")
        return [], 0, 0, ""


def fetch_oi_data_dual() -> dict | None:
    try:
        from core.shoonya_client import get_spot
        nf_ul  = get_spot("NIFTY")
        bnf_ul = get_spot("BANKNIFTY")
        if not nf_ul or not bnf_ul:
            return None
    except Exception:
        return None
    nf_nearest  = nearest_strike_nf(nf_ul)
    bnf_nearest = nearest_strike_bnf(bnf_ul)
    nf_oi,  nf_res,  nf_sup,  nf_exp  = _get_highest_oi_strikes("NIFTY",     10,  50, nf_nearest)
    bnf_oi, bnf_res, bnf_sup, bnf_exp = _get_highest_oi_strikes("BANKNIFTY", 10, 100, bnf_nearest)
    return {
        "NIFTY":     {"ltp": nf_ul,  "atm": nf_nearest,  "oi_data": nf_oi,
                      "max_resistance": nf_res, "max_support": nf_sup, "expiry": nf_exp},
        "BANKNIFTY": {"ltp": bnf_ul, "atm": bnf_nearest, "oi_data": bnf_oi,
                      "max_resistance": bnf_res, "max_support": bnf_sup, "expiry": bnf_exp},
    }


# ── Demo data ─────────────────────────────────────────────────────
def demo_data(symbol: str, cycle: int) -> dict:
    step        = 50    if symbol == "NIFTY" else 100
    base        = 22_450 if symbol == "NIFTY" else 48_500
    atm_iv_base = 11.5   if symbol == "NIFTY" else 13.0
    spot   = base + math.sin(cycle * 0.3) * 90 + random.uniform(-20, 20)
    atm    = round(spot / step) * step
    expiry = "27-Mar-2025"
    atm_iv = max(8.0, min(25.0, atm_iv_base + math.sin(cycle*0.15)*1.2 + random.uniform(-0.3,0.3)))
    items  = []
    for i in range(-10, 11):
        s     = atm + i * step
        iv_ce = max(5.0, round(atm_iv + 0.08*i**2 + 0.05*max(i,0)      + random.uniform(-0.2,0.2), 2))
        iv_pe = max(5.0, round(atm_iv + 0.25*i**2 + 0.15*abs(min(i,0)) + random.uniform(-0.2,0.2), 2))
        ce_oi = max(1000, int(800_000*math.exp(-0.08*(i+2)**2))) + random.randint(-5000,25000)*(cycle%3+1)
        pe_oi = max(1000, int(900_000*math.exp(-0.08*(i-2)**2))) + random.randint(-5000,25000)*(cycle%3+1)
        items.append({
            "strikePrice": float(s), "expiryDate": expiry,
            "CE": {"openInterest": ce_oi, "changeinOpenInterest": max(0,random.randint(500,35000)),
                   "lastPrice": max(1.0,round((200-max(0,s-atm))*0.9+random.uniform(-3,3),1)),
                   "totalTradedVolume": random.randint(20000,80000), "impliedVolatility": iv_ce},
            "PE": {"openInterest": pe_oi, "changeinOpenInterest": max(0,random.randint(500,35000)),
                   "lastPrice": max(1.0,round((200-max(0,atm-s))*0.9+random.uniform(-3,3),1)),
                   "totalTradedVolume": random.randint(20000,80000), "impliedVolatility": iv_pe},
        })
    return {"records": {"underlyingValue": round(spot,2),
                        "timestamp": now_ist().strftime("%d-%b-%Y %H:%M:%S"),
                        "expiryDates": [expiry, "03-Apr-2025", "24-Apr-2025"],
                        "data": items}}
