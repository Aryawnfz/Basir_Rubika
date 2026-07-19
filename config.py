import os

# ── Flask secret key ───────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("BASIR_SECRET", "change-this-in-production-!@#$%")

# ── Admin credentials (change before deploying) ────────────────────────────────
ADMIN_USER = os.environ.get("BASIR_USER", "admin")
ADMIN_PASS = os.environ.get("BASIR_PASS", "basir1234")

# ── File paths ─────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, "data")
ACCOUNTS_FILE  = os.path.join(DATA_DIR, "accounts.json")
PROFILES_DIR   = os.path.join(DATA_DIR, "profiles")   # ← هر اکانت یه پوشه Chrome profile جدا
RESULTS_FILE   = os.path.join(DATA_DIR, "last_results.json")

# ── Playwright settings ────────────────────────────────────────────────────────
RUBIKA_URL         = "https://web.rubika.ir"
SEARCH_TIMEOUT_MS  = 150_000
MAX_CONCURRENT     = 3   # persistent context سنگین‌تره، کمتر بذار
