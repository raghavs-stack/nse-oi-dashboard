# ════════════════════════════════════════════════════════════════
#  colab_runner.py  — Paste THIS into a Colab cell and run it.
#
#  SETUP (run once before this cell):
#    1. Upload the entire nse_oi_dashboard/ folder to Colab
#       OR use: !unzip nse_oi_dashboard_v5_3_modular.zip
#    2. Run this cell
#
#  DO NOT run main.py or nse_oi_dashboard.py directly —
#  this runner sets up sys.path and clears stale module caches first.
# ════════════════════════════════════════════════════════════════

import sys, os, importlib, shutil

# ── 1. Find where the dashboard folder is ────────────────────────
_candidates = [
    "/content/nse_oi_dashboard",
    "/content/drive/MyDrive/nse_oi_dashboard",
    os.path.join(os.getcwd(), "nse_oi_dashboard"),
    os.getcwd(),   # if you're already inside the folder
]

DASHBOARD_DIR = None
for p in _candidates:
    if os.path.exists(os.path.join(p, "main.py")):
        DASHBOARD_DIR = p
        break

if DASHBOARD_DIR is None:
    raise FileNotFoundError(
        "Cannot find nse_oi_dashboard/main.py\n"
        "Upload the ZIP and run:  !unzip nse_oi_dashboard_v5_3_modular.zip\n"
        "Then re-run this cell."
    )

print(f"✓ Dashboard found at: {DASHBOARD_DIR}")

# ── 2. Purge stale .pyc / __pycache__ so Python doesn't load old bytecode ──
for root, dirs, files in os.walk(DASHBOARD_DIR):
    if "__pycache__" in dirs:
        shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
for root, dirs, files in os.walk(DASHBOARD_DIR):
    for f in files:
        if f.endswith(".pyc"):
            os.remove(os.path.join(root, f))

# ── 3. Remove any old module registrations (handles Colab re-runs) ──
old_modules = [k for k in sys.modules
               if k in ("config","state","main") or
               k.startswith(("core.","signals.","display.","backtest."))]
for m in old_modules:
    del sys.modules[m]

# ── 4. Insert dashboard directory at front of path ────────────────
if DASHBOARD_DIR in sys.path:
    sys.path.remove(DASHBOARD_DIR)
sys.path.insert(0, DASHBOARD_DIR)

# ── 5. Install missing packages silently ─────────────────────────
os.system("pip install -q yfinance requests pandas matplotlib")

# ── 6. Verify we're loading the correct files ─────────────────────
import importlib.util
spec = importlib.util.spec_from_file_location(
    "core.nse_fetcher",
    os.path.join(DASHBOARD_DIR, "core", "nse_fetcher.py"))
print(f"✓ Loading nse_fetcher from: {spec.origin}")

# Quick sanity-check: the new code has a version sentinel
with open(spec.origin) as fh:
    src = fh.read()
if "NSE_FETCHER_VERSION" not in src:
    raise RuntimeError(
        "You have the OLD nse_fetcher.py (no version sentinel).\n"
        "Please re-upload the v5.3 ZIP and re-run this cell.\n"
        "Old file at: " + spec.origin
    )
print("✓ nse_fetcher.py is v5.4 (correct)")

# ── 7. Run the dashboard ──────────────────────────────────────────
print("\nStarting NSE OI Dashboard v5.3...\n")
import main
main.main()
