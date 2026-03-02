# colab_diagnose.py v4
# nseappid is set by JS — requests can't run JS.
# Fix: manually set nseappid = nsit value (standard NSE scraper trick).
import requests, json, time

_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
_API = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/option-chain",
    "X-Requested-With": "XMLHttpRequest",
}

s = requests.Session()

print("Step 1: market-data page -> nsit, Akamai cookies...")
r = s.get("https://www.nseindia.com/market-data/live-equity-market",
          headers={**_BROWSER, "Referer": "https://www.google.com/"}, timeout=15)
print(f"  {r.status_code}  jar={list(s.cookies.keys())}")
time.sleep(2.5)

print("Step 2: option-chain page...")
r = s.get("https://www.nseindia.com/option-chain",
          headers={**_BROWSER, "Referer": "https://www.nseindia.com/market-data/live-equity-market"},
          timeout=15)
print(f"  {r.status_code}  jar={list(s.cookies.keys())}")
time.sleep(2.0)

# THE FIX: nseappid is set by JavaScript. requests can't run JS.
# NSE's JS does: document.cookie = "nseappid=" + getCookie("nsit")
nsit = s.cookies.get("nsit", "")
if nsit:
    s.cookies.set("nseappid", nsit, domain=".nseindia.com")
    print(f"  nseappid manually set = nsit value ({nsit[:15]}...)")
    print(f"  jar now: {list(s.cookies.keys())}")
else:
    print("  ERROR: nsit not found! Cannot set nseappid.")

time.sleep(1.0)

print("\nStep 3: marketStatus (warms API token)...")
r = s.get("https://www.nseindia.com/api/marketStatus", headers=_API, timeout=12)
print(f"  {r.status_code}  open={json.loads(r.text).get('marketState',[{}])[0].get('marketStatus','?') if r.status_code==200 and r.text.startswith('{') else '?'}")
time.sleep(1.0)

print("\nStep 4: allIndices (spot check)...")
r = s.get("https://www.nseindia.com/api/allIndices", headers=_API, timeout=12)
if r.status_code == 200:
    d = json.loads(r.text)
    spots = [(x["index"], x["last"]) for x in d.get("data",[]) if x.get("index") in ("NIFTY 50","NIFTY BANK")]
    print(f"  Spots: {spots}")
time.sleep(0.5)

print("\nStep 5: OPTION CHAIN (the real test)...")
# Re-inject nseappid before the call (as _get_data does)
nsit = s.cookies.get("nsit", "")
if nsit:
    s.cookies.set("nseappid", nsit, domain=".nseindia.com")

r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
          headers=_API, timeout=15)
print(f"  {r.status_code}  body_len={len(r.text)}")

text = r.text.strip()
if text.startswith("<"):
    print("  RESULT: HTML (Cloudflare block — try later)")
elif text in ("{}","","null"):
    print(f"  RESULT: Still empty '{text}'")
    print("  The nsit=nseappid trick did not work for this session.")
    print("  This Colab IP may be blocked by NSE. Try: Runtime > Factory reset runtime")
else:
    try:
        d = json.loads(text)
        rec = d.get("records") or d.get("filtered") or (d if "data" in d else None)
        if rec and rec.get("data"):
            print(f"  underlyingValue: {rec.get('underlyingValue')}")
            print(f"  data items: {len(rec['data'])}")
            print(f"  expiryDates: {rec.get('expiryDates',[])[:3]}")
            item = rec["data"][0]
            print(f"  Sample CE_IV: {item.get('CE',{}).get('impliedVolatility','?')}")
            print(f"\n  *** DATA GOOD — dashboard will work! ***")
        else:
            print(f"  Unknown shape. Keys: {list(d.keys())}")
    except Exception as e:
        print(f"  JSON error: {e}  body: {text[:200]!r}")

print(f"\nFinal jar: {list(s.cookies.keys())}")
