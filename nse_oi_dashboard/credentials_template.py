# ════════════════════════════════════════════════════════════════
#  credentials_template.py
#  Copy this file to credentials.py and fill in your Shoonya details.
#  credentials.py is git-ignored — never commit it.
#
#  Where to find these values:
#    USER_ID      → your Shoonya login ID (e.g. "FA12345")
#    PASSWORD     → your Shoonya login password
#    TOTP_KEY     → the secret key shown when you set up 2FA in Shoonya app
#                   (the text/QR code secret, NOT the 6-digit code itself)
#                   Settings → Security → Authenticator → "Can't scan? Use key"
#    VENDOR_CODE  → from Shoonya API dashboard (api.shoonya.com)
#    API_SECRET   → from Shoonya API dashboard
#    IMEI         → any string, e.g. "abc1234"
# ════════════════════════════════════════════════════════════════

SHOONYA_USER_ID     = "YOUR_USER_ID"
SHOONYA_PASSWORD    = "YOUR_PASSWORD"
SHOONYA_TOTP_KEY    = "YOUR_TOTP_SECRET_KEY"   # secret key, not the 6-digit code
SHOONYA_VENDOR_CODE = "YOUR_VENDOR_CODE"
SHOONYA_API_SECRET  = "YOUR_API_SECRET"
SHOONYA_IMEI        = "abc1234"
