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

# ── صبر و تلاشِ جستجو ──────────────────────────────────────────────────────────
# سرورِ جستجوی روبیکا گاهی ۲ ثانیه و گاهی بیش از ۲۰ ثانیه طول می‌کشد تا گروهِ
# «پیام‌ها» را برگرداند؛ اگر کم صبر کنیم، جستجو «بدون نتیجه» به نظر می‌رسد.
RESULTS_TIMEOUT_MS   = 180_000   # حداکثر صبر برای ظاهر شدنِ اولین نتیجه
RESULTS_SETTLE_MS    = 8_000     # نتایج باید این‌قدر بی‌تغییر بمانند تا «کامل» حساب شوند
RESULTS_POLL_MS      = 400       # فاصلهٔ نمونه‌برداری از تعداد نتایج
QUERY_RETRIES        = 3         # چند بار کوئری دوباره ثبت شود اگر نتیجه‌ای نیامد
QUERY_RETRY_AFTER_MS = 25_000    # اگر تا این مدت نتیجه‌ای نیامد، کوئری را دوباره ثبت کن

# ── صبرِ لود شدنِ حبابِ پیام پس از کلیک روی نتیجه ───────────────────────────────
MESSAGE_LOAD_TIMEOUT_MS = 30_000  # حداکثر صبر برای لودِ کاملِ پیامِ هدف
MESSAGE_POLL_MS         = 150     # نمونه‌برداریِ سریع → به‌محضِ آماده شدن ادامه می‌دهیم
EXTRACT_ATTEMPTS        = 3       # چند بار برای یک نتیجه تلاش شود (کلیک/استخراج)
LINK_TIMEOUT_MS         = 4_000   # حداکثر صبر برای کپی‌شدنِ لینکِ پیام در کلیپ‌بورد
MENU_TIMEOUT_MS         = 3_000   # حداکثر صبر برای باز شدنِ منوی کلیک‌راست

# ── کاوشِ کانال‌ها ─────────────────────────────────────────────────────────────
# روبیکا صفحهٔ عمومیِ کانال ندارد، پس اطلاعاتِ کانال و پست‌ها از داخلِ وب‌اپِ
# احرازشده خوانده می‌شود؛ همان صبرِ سخاوتمندانهٔ جستجو اینجا هم لازم است.
EXPLORE_MAX_POSTS         = 50       # سقفِ تعدادِ پستِ قابل بررسی در هر کانال
CHANNEL_SEARCH_TIMEOUT_MS = 60_000   # صبر برای ظاهر شدنِ کانال در نتایجِ جستجو
CHANNEL_OPEN_TIMEOUT_MS   = 45_000   # صبر برای لود شدنِ پیام‌های کانالِ باز‌شده
POSTS_LOAD_TIMEOUT_MS     = 180_000  # صبر برای بارگذاریِ تعدادِ خواسته‌شده از پست‌ها
POSTS_POLL_MS             = 400      # فاصلهٔ نمونه‌برداری در کاوش
PROFILE_PANEL_TIMEOUT_MS  = 15_000   # صبر برای باز شدنِ پنلِ «اطلاعات کانال»
