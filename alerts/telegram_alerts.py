# ════════════════════════════════════════════════════════════════
#  alerts/telegram_alerts.py  v5.6
#  Push trade signals and breakout alerts to Telegram.
#
#  Setup (one-time):
#    1. Message @BotFather → /newbot → copy token
#    2. Message your bot once, then visit:
#       https://api.telegram.org/bot<TOKEN>/getUpdates
#       to find your chat_id
#    3. Add to credentials.py:
#         TELEGRAM_BOT_TOKEN = "123456:ABC-..."
#         TELEGRAM_CHAT_ID   = "987654321"
# ════════════════════════════════════════════════════════════════

import requests
from datetime import datetime

_ENABLED = False
_TOKEN   = ""
_CHAT_ID = ""
_last_info_alert: dict = {}   # symbol → datetime of last non-taken alert


def init_telegram() -> bool:
    """Load Telegram credentials from credentials.py. Call once at startup."""
    global _ENABLED, _TOKEN, _CHAT_ID
    try:
        import credentials as creds
        token   = getattr(creds, "TELEGRAM_BOT_TOKEN", "")
        chat_id = getattr(creds, "TELEGRAM_CHAT_ID",   "")
        if not token or not chat_id or "YOUR" in token:
            print("  Telegram: not configured (optional — set TELEGRAM_BOT_TOKEN in credentials.py)")
            return False
        _TOKEN   = token
        _CHAT_ID = chat_id
        _ENABLED = True
        print(f"  Telegram: configured ✓  chat_id={chat_id}")
        return True
    except ImportError:
        return False


def _send(text: str):
    """Raw send — fire and forget, never raises."""
    if not _ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": _CHAT_ID, "text": text,
                                 "parse_mode": "HTML"}, timeout=8)
    except Exception as e:
        print(f"  Telegram send error: {e}")


def send_signal(signal, adv: dict = None):
    """
    Send a trade signal message.
    - Taken signals: always send immediately (real money on the line).
    - Informational (score≥70 but not taken): throttled to one per
      INFO_ALERT_COOLDOWN_MINS per symbol to prevent Telegram flood.
    """
    if not _ENABLED:
        return

    s = signal

    if not s.taken:
        # Rate-limit informational alerts per symbol
        try:
            from config import INFO_ALERT_COOLDOWN_MINS
        except ImportError:
            INFO_ALERT_COOLDOWN_MINS = 30
        sym_key = getattr(s, "symbol", "") or "DEFAULT"
        last    = _last_info_alert.get(sym_key)
        if last is not None:
            elapsed_mins = (datetime.now() - last).total_seconds() / 60
            if elapsed_mins < INFO_ALERT_COOLDOWN_MINS:
                return  # still within cooldown window — skip silently
        _last_info_alert[sym_key] = datetime.now()

    taken = "✅ TRADE TAKEN" if s.taken else "ℹ️ SIGNAL (not taken)"
    bias_icon = "🟢" if s.bias == "BULLISH" else ("🔴" if s.bias == "BEARISH" else "🟡")

    sym_label = getattr(s, "symbol", "") or ""
    sym_prefix = f"{sym_label} | " if sym_label else ""
    lines = [
        f"<b>{sym_prefix}{bias_icon} {s.bias} — {taken}</b>",
        f"🕐 {datetime.now().strftime('%H:%M IST')}  |  Score: <b>{s.score}/100</b>",
        f"Spot: ₹{s.spot:,.2f}  |  PCR: {s.pcr:.3f}",
        f"ATM IV: {s.atm_iv:.2f}%  IVR: {s.ivr}  IVP: {s.ivp}"
        if s.atm_iv is not None else
        f"ATM IV: warming…  IVR: {s.ivr}  IVP: {s.ivp}",
        "",
        f"📌 <b>Recommendations:</b>",
        f"  1. {s.rec1.strike} {s.rec1.opt_type} @ ₹{s.rec1.premium}  SL:{s.rec1.sl}  T:{s.rec1.target}",
        f"  2. {s.rec2.strike} {s.rec2.opt_type} @ ₹{s.rec2.premium}  SL:{s.rec2.sl}  T:{s.rec2.target}",
        f"  3. {s.rec3.strike} {s.rec3.opt_type} @ ₹{s.rec3.premium}  SL:{s.rec3.sl}  T:{s.rec3.target}",
    ]

    if adv:
        g  = adv.get("gamma",    {})
        sm = adv.get("smart_money", {})
        dh = adv.get("dealer",   {})
        lines += [
            "",
            f"📊 <b>Analytics:</b>",
            f"  GEX: {g.get('regime','N/A')}  Flip@{g.get('flip_level','N/A')}",
            f"  Dealer: {dh.get('bias','N/A')}  SmartMoney: {sm.get('dominant','N/A')}",
        ]

    if s.skip_reason and not s.taken:
        lines.append(f"\n⚠️ Skipped: {s.skip_reason}")

    _send("\n".join(lines))


def send_breakout(symbol: str, spot: float, breakout_signal: str,
                  resistance: int, support: int):
    """Send breakout/breakdown alert immediately."""
    if not _ENABLED:
        return
    icon = "⚡🔼" if "Upside" in breakout_signal else "⚡🔽"
    text = (
        f"{icon} <b>{breakout_signal}</b>\n"
        f"{symbol}  Spot: ₹{spot:,.2f}\n"
        f"Resistance: ₹{resistance:,}  |  Support: ₹{support:,}\n"
        f"🕐 {datetime.now().strftime('%H:%M IST')}"
    )
    _send(text)


def send_roc_alert(symbol: str, alerts: list):
    """Send OI rate-of-change spike alerts."""
    if not _ENABLED or not alerts:
        return
    text = f"🚨 <b>OI Spike — {symbol}</b>\n" + "\n".join(f"  • {a}" for a in alerts)
    _send(text)


def send_eod_summary(symbol: str, wins: int, losses: int, total: int, final_spot: float):
    """Send end-of-day performance summary."""
    if not _ENABLED:
        return
    rate = f"{wins/total*100:.0f}%" if total > 0 else "N/A"
    text = (
        f"📋 <b>EOD Summary — {symbol}</b>\n"
        f"Signals: {total}  Wins: {wins}  Losses: {losses}  Rate: {rate}\n"
        f"Final Spot: ₹{final_spot:,.2f}\n"
        f"🕐 {datetime.now().strftime('%d %b %Y')}"
    )
    _send(text)
