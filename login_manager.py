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

── نکتهٔ پلتفرم ───────────────────────────────────────────────────────────────
وب روبیکا (web.rubika.ir) یک کلاینت مبتنی بر Telegram Web K است، پس مسیر
ورود دو مرحله دارد: صفحهٔ شماره (.page-sign) و صفحهٔ کد تأیید (.page-authCode).
گاهی ابتدا صفحهٔ QR (.page-signQR) نمایش داده می‌شود که باید روی گزینهٔ
«ورود با شماره تلفن» کلیک کرد.
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

# نشانه‌های لاگین موفق (صفحهٔ اصلی چت‌ها)
LOGGED_IN_SELECTORS = [
    "#column-center",
    "#column-left .chatlist",
    ".chatlist-container",
    ".chatlist",
    ".sidebar-header .input-search-input",
    "#column-left",
]

# دکمهٔ «ورود با شماره تلفن» در صفحهٔ QR
PHONE_SWITCH_SELECTORS = [
    ".page-signQR .btn-primary",
    "button:has-text('شماره')",
    "button:has-text('تلفن')",
    "button:has-text('phone' i)",
    "a:has-text('شماره')",
]

# فیلد شمارهٔ تلفن (صفحهٔ .page-sign)
PHONE_SELECTORS = [
    ".page-sign .input-field-input",
    "input[type='tel']",
    ".input-field-phone .input-field-input",
    "input[placeholder*='شماره']",
    "input[placeholder*='phone' i]",
    "input[inputmode='tel']",
]

# فیلد کد تأیید (صفحهٔ .page-authCode)
OTP_SELECTORS = [
    ".page-authCode .input-field-input",
    ".login-phone-code-input-field input",
    ".input-field-code input",
    "input[inputmode='numeric']",
    "input[type='number']",
    "input[maxlength='5']",
    "input[maxlength='6']",
    "input[placeholder*='کد']",
    "input[placeholder*='code' i]",
]

NEXT_BTN_SELECTORS = [
    ".page-sign .btn-primary",
    "button[type='submit']",
    "button:has-text('ادامه')",
    "button:has-text('بعدی')",
    "button:has-text('تایید')",
    "button:has-text('Next')",
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
    """اولین سلکتوری که در بازهٔ timeout ظاهر شود را برمی‌گرداند (یا None)."""
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout)
            if el:
                return el
        except PwTimeout:
            continue
        except Exception:
            continue
    return None


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

            # ── اگر صفحهٔ QR بود، به ورود با شماره سوییچ کن ────────────
            phone_el = await _first_visible(page, PHONE_SELECTORS, timeout=5_000)
            if not phone_el:
                switch = await _first_visible(page, PHONE_SWITCH_SELECTORS, timeout=5_000)
                if switch:
                    try:
                        await switch.click()
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
                phone_el = await _first_visible(page, PHONE_SELECTORS, timeout=8_000)

            if not phone_el:
                _write_state(account_id, ST_ERROR,
                             error="فیلد شماره تلفن پیدا نشد. دوباره تلاش کنید.")
                await ctx.close()
                return

            # ── وارد کردن شماره ─────────────────────────────────────────
            _write_state(account_id, ST_PHONE, "در حال وارد کردن شماره...")
            await phone_el.click()
            try:
                await phone_el.fill("")
            except Exception:
                pass
            await phone_el.type(phone, delay=40)
            await asyncio.sleep(0.6)
            # tweb با معتبر شدن شماره دکمهٔ ادامه را نشان می‌دهد؛ هم دکمه هم Enter
            next_btn = await _first_visible(page, NEXT_BTN_SELECTORS, timeout=4_000)
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
