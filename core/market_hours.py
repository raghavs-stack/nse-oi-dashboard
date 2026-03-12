# ════════════════════════════════════════════════════════════════
#  core/market_hours.py
#  IST timezone helpers, market-open checks, next-open string.
# ════════════════════════════════════════════════════════════════

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


def is_market_open() -> bool:
    n = now_ist()
    if n.weekday() >= 5:            # Saturday / Sunday
        return False
    o = n.replace(hour=9,  minute=15, second=0, microsecond=0)
    c = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return o <= n <= c


def is_eod() -> bool:
    """True after 15:20 on weekdays — triggers EOD backtest."""
    n = now_ist()
    return n.weekday() < 5 and n.hour == 15 and n.minute >= 20


def next_open_str() -> str:
    n = now_ist()
    if n.weekday() < 5:
        o = n.replace(hour=9, minute=15, second=0, microsecond=0)
        if n < o:
            mins = int((o - n).seconds // 60)
            return f"Today at 09:15 IST ({mins} min away)"
        skip = 3 if n.weekday() == 4 else 1
    else:
        skip = (7 - n.weekday()) % 7 or 7
    nxt = (n + timedelta(days=skip)).replace(
        hour=9, minute=15, second=0, microsecond=0)
    return nxt.strftime("%A %d %b at 09:15 IST")
