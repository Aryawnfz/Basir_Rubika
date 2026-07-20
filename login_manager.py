"""
مدیریت ورود headless به روبیکا — نسخه سازگار با Gunicorn/Multi-Worker

مشکل قبلی:
  _sessions یه dict در RAM بود. Gunicorn چند process جدا اجرا می‌کنه.
  درخواست start روی worker-1 می‌رفت، polling روی worker-2 — dict خالی → خطا.

راه‌حل (File-based IPC):
  وضعیت session و OTP در فایل‌های دیسک نوشته می‌شن.
  هر worker می‌تونه بخونه/بنویسه بدون هماهنگی.

  data/login_sessions/<id>.json  ← وضعیت (status/message/error)
  data/login_sessions/<id>.otp   ← OTP کاربر (یه عدد چند رقمی)

background thread فقط در workerی که start_login رو گرفته اجرا میشه.
بقیه worker ها فقط فایل‌ها رو می‌خونن/می‌نویسن.

── نکتهٔ پلتفرم (سلکتورهای واقعی) ──────────────────────────────────────────────
وب روبیکا (web.rubika.ir) یک اپلیکیشن Angular است. مسیر ورود دو مرحله دارد:
  ۱) صفحهٔ شماره: input[name="phone_number"] + دکمهٔ «بعدی» (button.btn-primary)
     توجه: یک input.input-field-input دیگر (نمایش کشور «ایران») disabled است؛
     نباید سراغش رفت — علت خطای «element is not enabled» در نسخهٔ قبلی همین بود.
  ۲) صفحهٔ کد: input[name="phone_code"] — با کامل شدن تعداد ارقام، خودکار وارد می‌شود.
کشور پیش‌فرض +98 است، پس شماره باید بدون کد کشور و بدون صفرِ ابتدایی وارد شود.
"""

import asyncio
import json
import os
import threading
import time
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PwTimeout

from config import RUBIKA_URL, DATA_DIR

# ── دایرکتوری session ها ──────────────────────────────────────────────────────
SESSIONS_DIR = os.path.join(DATA_DIR, "login_sessions")


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _state_path(account_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{account_id}.json")


def _otp_path(account_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{account_id}.otp")


def _write_state(account_id: str, status: str, message: str = "", error: str = ""):
    _ensure_dir()
    tmp = _state_path(account_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"status": status, "message": message, "error": error}, f)
    os.replace(tmp, _state_path(account_id))  # atomic write


# ── ثابت‌های Playwright ────────────────────────────────────────────────────────
_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--lang=fa-IR",
]
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# نشانه‌های لاگین موفق (چیدمان اصلی چت‌ها بعد از ورود)
LOGGED_IN_SELECTORS = [
    ".chatlist-container",
    ".chats-container",
    ".chatlist-chat",
    ".chatlist",
    ".main-columns",
    "#column-left .input-search-input",
    ".sidebar-search .input-search-input",
]

# فیلد شمارهٔ تلفن — دقیقاً input فعال (نه فیلد کشورِ disabled)
PHONE_SELECTORS = [
    "input[name='phone_number']",
    "input.input-field-input[type='tel']:not([disabled])",
    "input[type='tel']:not([name='phone_country']):not([disabled])",
]

# فیلد کد تأیید
OTP_SELECTORS = [
    "input[name='phone_code']",
    ".login-phone-code-input-field input:not([disabled])",
    ".input-field-code input:not([disabled])",
]

# دکمهٔ «بعدی» بعد از وارد کردن شماره
NEXT_BTN_SELECTORS = [
    "button.btn-primary:has-text('بعدی')",
    "button.btn-primary:has-text('ادامه')",
    "button.btn-primary:has-text('تایید')",
    "button.btn-primary",
    "button[type='submit']",
]

ST_STARTING  = "starting"
ST_PHONE     = "entering_phone"
ST_WAIT_OTP  = "waiting_otp"
ST_VERIFYING = "verifying"
ST_SUCCESS   = "success"
ST_ERROR     = "error"
ST_TIMEOUT   = "timeout"

# ── Background loop (per-process, lazy init) ──────────────────────────────────
_loop: Optional[asyncio.AbstractEventLoop] = None
_bg_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _bg_thread
    with _loop_lock:
        if _loop is None or _bg_thread is None or not _bg_thread.is_alive():
            _loop = asyncio.new_event_loop()
            _bg_thread = threading.Thread(
                target=_loop.run_forever,
                daemon=True,
                name="basir-login-loop",
            )
            _bg_thread.start()
            time.sleep(0.1)
    return _loop


async def _first_visible(page, selectors, timeout=8_000):
    """اولین سلکتوری که visible و enabled شود را برمی‌گرداند (یا None)."""
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el and await el.is_enabled():
                return el
        except PwTimeout:
            continue
        except Exception:
            continue
    return None


def _normalize_phone(phone: str) -> str:
    """شماره را برای فیلد روبیکا آماده می‌کند: فقط ارقام، بدون کد کشور و صفرِ ابتدایی."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98") and len(digits) > 10:
        digits = digits[2:]
    digits = digits.lstrip("0")
    return digits


# ══════════════════════════════════════════════════════════════════════════════
# Coroutine — فقط در یه worker اجرا میشه، state رو روی فایل می‌نویسه
# ══════════════════════════════════════════════════════════════════════════════
async def _do_login(account: dict) -> None:
    account_id    = account["id"]
    user_data_dir = account["user_data_dir"]
    phone         = account["phone"]

    os.makedirs(user_data_dir, exist_ok=True)
    _write_state(account_id, ST_PHONE, "در حال باز کردن روبیکا...")

    ctx = None
    try:
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=True,
                args=_CHROME_ARGS,
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="fa-IR",
                timezone_id="Asia/Tehran",
            )

            pages = ctx.pages
            page = pages[0] if pages else await ctx.new_page()

            await page.goto(RUBIKA_URL, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)

            # ── پیدا کردن فیلد شماره (input فعال، نه فیلد کشورِ disabled) ──
            phone_el = await _first_visible(page, PHONE_SELECTORS, timeout=15_000)
            if not phone_el:
                _write_state(account_id, ST_ERROR,
                             error="فیلد شماره تلفن پیدا نشد. دوباره تلاش کنید.")
                await ctx.close()
                return

            # ── وارد کردن شماره (بدون کد کشور و بدون صفرِ ابتدایی) ────────
            _write_state(account_id, ST_PHONE, "در حال وارد کردن شماره...")
            phone_norm = _normalize_phone(phone)
            await phone_el.click()
            try:
                await phone_el.fill("")
            except Exception:
                pass
            await phone_el.fill(phone_norm)
            await asyncio.sleep(0.6)
            # دکمهٔ «بعدی» با معتبر شدن شماره فعال می‌شود
            next_btn = await _first_visible(page, NEXT_BTN_SELECTORS, timeout=5_000)
            if next_btn:
                try:
                    await next_btn.click()
                except Exception:
                    await page.keyboard.press("Enter")
            else:
                await page.keyboard.press("Enter")
            await asyncio.sleep(2)

            # ── منتظر OTP — فایل رو poll می‌کنیم ────────────────────────
            _write_state(account_id, ST_WAIT_OTP, "کد تایید ارسال شد. کد را وارد کنید.")
            otp_file = _otp_path(account_id)

            otp_code = None
            for _ in range(300):   # 5 دقیقه
                if os.path.exists(otp_file):
                    try:
                        with open(otp_file, "r") as f:
                            otp_code = f.read().strip()
                        os.remove(otp_file)
                    except Exception:
                        pass
                    if otp_code:
                        break
                await asyncio.sleep(1)

            if not otp_code:
                _write_state(account_id, ST_TIMEOUT,
                             error="زمان وارد کردن کد به پایان رسید.")
                await ctx.close()
                return

            # ── وارد کردن OTP ────────────────────────────────────────────
            _write_state(account_id, ST_VERIFYING, "در حال تایید کد...")
            await asyncio.sleep(0.5)

            otp_el = await _first_visible(page, OTP_SELECTORS, timeout=8_000)
            if not otp_el:
                _write_state(account_id, ST_ERROR,
                             error="فیلد کد تایید پیدا نشد.")
                await ctx.close()
                return

            await otp_el.click()
            # tweb با کامل شدن کد به‌صورت خودکار تأیید می‌کند
            await otp_el.type(otp_code, delay=60)
            await asyncio.sleep(0.8)
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
            await asyncio.sleep(1)

            # ── انتظار برای لاگین کامل ───────────────────────────────────
            logged_in = False
            for _ in range(40):
                for sel in LOGGED_IN_SELECTORS:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            logged_in = True
                            break
                    except Exception:
                        pass
                if logged_in:
                    break
                await asyncio.sleep(1)

            if not logged_in:
                _write_state(account_id, ST_ERROR,
                             error="کد نادرست بود یا لاگین انجام نشد.")
                await ctx.close()
                return

            # ── ذخیره پروفایل ────────────────────────────────────────────
            _write_state(account_id, ST_VERIFYING, "در حال ذخیره پروفایل...")
            await asyncio.sleep(4)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            await asyncio.sleep(2)
            await ctx.close()

            _write_state(account_id, ST_SUCCESS, "وارد شدید! اکانت آماده جستجو است.")

    except Exception as exc:
        _write_state(account_id, ST_ERROR, error=str(exc))
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def start_login(account: dict) -> None:
    """شروع login در background این worker."""
    loop = _ensure_loop()
    # اگه session قبلی داشت و هنوز waiting_otp هست، ری‌استارت نکن
    cur = get_status(account["id"])
    if cur["status"] in (ST_WAIT_OTP, ST_VERIFYING, ST_PHONE):
        return
    _write_state(account["id"], ST_STARTING, "در حال راه‌اندازی...")
    asyncio.run_coroutine_threadsafe(_do_login(account), loop)


def submit_otp(account_id: str, otp_code: str) -> bool:
    """OTP رو روی دیسک بنویس — هر worker می‌تونه این رو صدا بزنه."""
    _ensure_dir()
    try:
        with open(_otp_path(account_id), "w") as f:
            f.write(otp_code.strip())
        return True
    except Exception:
        return False


def get_status(account_id: str) -> dict:
    """وضعیت رو از فایل بخون — هر worker می‌تونه بخونه."""
    path = _state_path(account_id)
    if not os.path.exists(path):
        return {"status": "not_found", "message": "", "error": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "not_found", "message": "", "error": ""}


def cleanup(account_id: str) -> None:
    """فایل‌های session رو پاک کن."""
    for path in [_state_path(account_id), _otp_path(account_id)]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
