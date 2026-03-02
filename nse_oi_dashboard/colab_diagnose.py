# ════════════════════════════════════════════════════════════════
#  colab_diagnose.py
#  Paste into a Colab cell BEFORE running the dashboard.
#  Shows exactly what NSE is returning so we can debug.
# ════════════════════════════════════════════════════════════════

import requests, json, time

NSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/option-chain",
}

s = requests.Session()
s.headers.update(NSE_HEADERS)
cookies = {}

print("Step 1: hitting homepage...")
r = s.get("https://www.nseindia.com/", timeout=15)
cookies.update(r.cookies)
print(f"  status={r.status_code}  cookies={list(r.cookies.keys())}")
time.sleep(2.5)

print("Step 2: hitting market page...")
r = s.get("https://www.nseindia.com/market-data/live-equity-market", timeout=12)
cookies.update(r.cookies)
print(f"  status={r.status_code}  cookies={list(r.cookies.keys())}")
time.sleep(1.5)

print("Step 3: hitting option-chain page...")
r = s.get("https://www.nseindia.com/option-chain", timeout=12)
cookies.update(r.cookies)
print(f"  status={r.status_code}  cookies={list(r.cookies.keys())}")
time.sleep(2.0)

print("Step 4: hitting marketStatus API...")
r = s.get("https://www.nseindia.com/api/marketStatus", headers=NSE_HEADERS, cookies=cookies, timeout=12)
cookies.update(r.cookies)
print(f"  status={r.status_code}  body[:80]={r.text[:80]!r}")
time.sleep(1.0)

print("\nStep 5: allIndices (spot price test)...")
r = s.get("https://www.nseindia.com/api/allIndices", headers=NSE_HEADERS, cookies=cookies, timeout=12)
cookies.update(r.cookies)
print(f"  status={r.status_code}")
if r.status_code == 200:
    try:
        d = json.loads(r.text)
        spots = [(x["index"], x["last"]) for x in d.get("data",[])
                 if x.get("index") in ("NIFTY 50","NIFTY BANK")]
        print(f"  ✓ SPOTS: {spots}")
    except:
        print(f"  body[:200]={r.text[:200]!r}")
else:
    print(f"  body[:200]={r.text[:200]!r}")

time.sleep(1.0)

print("\nStep 6: option chain (main call)...")
r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
          headers=NSE_HEADERS, cookies=cookies, timeout=15)
cookies.update(r.cookies)
print(f"  status={r.status_code}  body_len={len(r.text)}")

text = r.text.strip()
if text.startswith("<"):
    print("  ✗ GOT HTML — Cloudflare is blocking this Colab IP")
    print(f"  First 300 chars: {text[:300]}")
elif text == "" or text == "{}":
    print("  ✗ GOT EMPTY/EMPTY-DICT — Cloudflare rate-limit response")
else:
    try:
        d = json.loads(text)
        print(f"  Top-level keys: {list(d.keys())}")
        if "records" in d:
            rec = d["records"]
            print(f"  records keys: {list(rec.keys())}")
            print(f"  underlyingValue: {rec.get('underlyingValue')}")
            print(f"  expiryDates: {rec.get('expiryDates', [])[:3]}")
            print(f"  data items: {len(rec.get('data', []))}")
            print(f"  ✓ DATA LOOKS GOOD — dashboard should work")
        elif "filtered" in d:
            print(f"  filtered keys: {list(d['filtered'].keys())}")
            print(f"  underlyingValue: {d['filtered'].get('underlyingValue')}")
            print(f"  ✓ Shape B response — dashboard will normalize this")
        else:
            print(f"  ✗ Unknown shape — full response[:500]: {text[:500]}")
    except Exception as e:
        print(f"  JSON parse error: {e}")
        print(f"  body[:300]: {text[:300]!r}")

print("\n─── All cookies acquired ───")
print({k: v[:20]+"…" if len(v)>20 else v for k,v in cookies.items()})
