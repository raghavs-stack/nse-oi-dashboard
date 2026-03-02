# ════════════════════════════════════════════════════════════════
#  colab_diagnose.py  v2
#  Tests exact warmup sequence used by nse_fetcher.py v5.3.2
#  Paste into a Colab cell to verify connectivity before running dashboard.
# ════════════════════════════════════════════════════════════════

import requests, json, time

_BROWSER_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}
_API_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/option-chain",
    "X-Requested-With": "XMLHttpRequest",
}

s = requests.Session()

print("=" * 60)
print("NSE CONNECTIVITY DIAGNOSTIC  (nse_fetcher v5.3.2 sequence)")
print("=" * 60)

print("\nStep 1: market-data page (-> nsit, _abck, bm_sz)...")
r = s.get("https://www.nseindia.com/market-data/live-equity-market",
           headers={**_BROWSER_HEADERS, "Referer": "https://www.google.com/"}, timeout=15)
print(f"  HTTP {r.status_code}  new cookies: {list(r.cookies.keys())}  jar: {list(s.cookies.keys())}")
time.sleep(2.5)

print("\nStep 2: option-chain page (-> nseappid)...")
r = s.get("https://www.nseindia.com/option-chain",
           headers={**_BROWSER_HEADERS, "Referer": "https://www.nseindia.com/market-data/live-equity-market"},
           timeout=15)
print(f"  HTTP {r.status_code}  new cookies: {list(r.cookies.keys())}  jar: {list(s.cookies.keys())}")
print(f"  nseappid present: {'YES' if 'nseappid' in s.cookies else 'NO - may fail'}")
time.sleep(2.5)

print("\nStep 3: marketStatus API...")
r = s.get("https://www.nseindia.com/api/marketStatus", headers=_API_HEADERS, timeout=12)
print(f"  HTTP {r.status_code}  body[:60]: {r.text[:60]!r}")
time.sleep(1.0)

print("\nStep 4: allIndices (spot check)...")
r = s.get("https://www.nseindia.com/api/allIndices", headers=_API_HEADERS, timeout=12)
print(f"  HTTP {r.status_code}")
if r.status_code == 200:
    try:
        d = json.loads(r.text)
        spots = [(x["index"], x["last"]) for x in d.get("data", [])
                 if x.get("index") in ("NIFTY 50", "NIFTY BANK")]
        print(f"  Spots: {spots}")
    except: print(f"  body[:100]: {r.text[:100]!r}")
time.sleep(1.0)

print("\nStep 5: OPTION CHAIN (the real test)...")
# Refresh nseappid exactly as _get_data does before every call
s.get("https://www.nseindia.com/option-chain", headers=_BROWSER_HEADERS, timeout=8)
time.sleep(0.5)

r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
           headers=_API_HEADERS, timeout=15)
print(f"  HTTP {r.status_code}  body_len={len(r.text)}")

text = r.text.strip()
if text.startswith("<"):
    print("  RESULT: GOT HTML - Cloudflare blocking. Try again in a few minutes.")
elif text in ("{}", "", "null"):
    print(f"  RESULT: EMPTY ('{text}') - nseappid missing or expired.")
    print(f"  Full cookie jar: {dict(s.cookies)}")
else:
    try:
        d = json.loads(text)
        rec = (d.get("records") or d.get("filtered") or
               (d if "data" in d and "underlyingValue" in d else None))
        if rec:
            print(f"  underlyingValue: {rec.get('underlyingValue')}")
            print(f"  expiryDates[:3]: {rec.get('expiryDates', [])[:3]}")
            print(f"  data items: {len(rec.get('data', []))}")
            if rec.get("data"):
                item = rec["data"][0]
                ce_iv = item.get("CE", {}).get("impliedVolatility", "MISSING")
                print(f"  Sample CE_IV at first strike: {ce_iv}")
            print(f"  RESULT: DATA GOOD - dashboard should work!")
        else:
            print(f"  RESULT: Unknown shape. Keys: {list(d.keys())}")
    except Exception as e:
        print(f"  RESULT: JSON error: {e}  body[:200]: {text[:200]!r}")

print(f"\nFinal cookie jar: {list(s.cookies.keys())}")
print(f"nseappid: {'PRESENT' if 'nseappid' in s.cookies else 'MISSING'}")
print("=" * 60)
