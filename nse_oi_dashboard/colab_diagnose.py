import json, requests, time
from datetime import datetime
import pytz

now = datetime.now(pytz.timezone("Asia/Kolkata"))
print(f"IST: {now.strftime('%I:%M %p')} ({now.strftime('%A')})")
is_open = now.weekday() < 5 and 9*60+15 <= now.hour*60+now.minute <= 15*60+30
print(f"Market: {'OPEN' if is_open else 'CLOSED'}")
if not is_open:
    print("NSE returns empty data when market is closed.")
    print("Run this script between 9:15 AM - 3:30 PM IST on weekdays.")
    exit()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PAGE = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate, br"}
API  = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/option-chain", "X-Requested-With": "XMLHttpRequest"}

s = requests.Session()
print("\nStep 1: homepage...")
r = s.get("https://www.nseindia.com/", headers=PAGE, timeout=15)
print(f"  HTTP {r.status_code}  cookies={list(s.cookies.keys())}")
time.sleep(2)

print("Step 2: option-chain page...")
r = s.get("https://www.nseindia.com/option-chain",
          headers={**PAGE, "Referer": "https://www.nseindia.com/"}, timeout=15)
print(f"  HTTP {r.status_code}  cookies={list(s.cookies.keys())}")
time.sleep(2)

print("Step 3: option chain API...")
s.get("https://www.nseindia.com/option-chain",
      headers={**PAGE, "Referer": "https://www.nseindia.com/"}, timeout=8)
r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
          headers=API, timeout=15)
print(f"  HTTP {r.status_code}  body_len={len(r.text)}")
text = r.text.strip()
if text in ("{}", "", "null") or text.startswith("<"):
    print(f"  EMPTY/BLOCKED: '{text[:50]}'")
    print("  Share this output so we can debug further.")
else:
    try:
        d = json.loads(text)
        rec = d.get("records") or d.get("filtered") or (d if "data" in d else None)
        if rec and rec.get("data"):
            print(f"  Spot: {rec.get('underlyingValue')}")
            print(f"  Strikes: {len(rec['data'])}")
            print(f"  Expiries: {rec.get('expiryDates',[])[:3]}")
            print("\n  DATA GOOD - run: python main.py")
        else:
            print(f"  Unknown shape: {list(d.keys())}")
    except Exception as e:
        print(f"  Error: {e}")
