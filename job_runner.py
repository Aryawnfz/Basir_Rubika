"""
اجراکنندهٔ Jobهای جستجو — هر job مستقل و هم‌زمان (concurrent) با سایر Jobها اجرا می‌شود.

برخلاف یک «صف ترتیبی»، اینجا هیچ قفل سراسری بین Jobها وجود ندارد:
اگر ۵ درخواست جستجو ارسال شود، ۵ اجرای مستقل و هم‌زمان شروع می‌شود —
هرکدام نمونهٔ مرورگر خودش را برای اکانت‌های روبیکا باز می‌کند.

برای جلوگیری از تصادم روی پروفایل Chrome یک اکانت مشترک (اگر دو Job
هم‌زمان بخواهند از همان اکانت استفاده کنند)، قفل per-account داخل
rubika_search.py اعمال شده — نه قفل کلی روی همهٔ Jobها. یعنی جستجوهای
روی اکانت‌های متفاوت کاملاً موازی پیش می‌روند.

نکته: این background loop به‌صورت per-process است و فرض می‌کند Gunicorn
با workers=1 اجرا می‌شود (همان‌طور که در gunicorn.conf.py تنظیم شده).
"""
import asyncio
import threading
import time
from concurrent.futures import Future
from typing import Optional

import accounts as acc_store
import jobs as job_store
from rubika_search import search_all_accounts, profile_has_data

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()

# نگه‌داری future هر job در حال اجرا — برای امکان لغو فوری از طریق دکمهٔ «حذف»
_futures: dict[str, Future] = {}
_futures_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _loop_lock:
        if _loop is None or _thread is None or not _thread.is_alive():
            _loop = asyncio.new_event_loop()
            _thread = threading.Thread(
                target=_loop.run_forever, daemon=True, name="basir-job-runner",
            )
            _thread.start()
            time.sleep(0.1)
    return _loop


async def _run_job(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if not job:
        return

    job_store.update_job(job_id, status=job_store.ST_RUNNING,
                          message="در حال جستجو در اکانت‌های روبیکا...")

    ready = [a for a in acc_store.load_accounts()
             if profile_has_data(a.get("user_data_dir", ""))]
    if not ready:
        job_store.update_job(job_id, status=job_store.ST_ERROR,
                              error="هیچ اکانت آماده‌ای موجود نیست.")
        return

    try:
        results = await search_all_accounts(
            ready, job["query"], job.get("date_from", ""), job.get("date_to", "")
        )
        job_store.update_job(
            job_id, status=job_store.ST_DONE, message="تکمیل شد",
            results=results, result_count=len(results),
        )
    except asyncio.CancelledError:
        # کاربر روی «حذف» کلیک کرده — مرورگرها در finally بسته شده‌اند،
        # خود فایل job معمولاً قبلاً توسط route حذف شده؛ کاری لازم نیست.
        raise
    except Exception as exc:
        job_store.update_job(job_id, status=job_store.ST_ERROR, error=str(exc))
    finally:
        with _futures_lock:
            _futures.pop(job_id, None)


def start_job(job_id: str) -> None:
    """
    job را در پس‌زمینهٔ همین process زمان‌بندی می‌کند.
    چون هیچ قفلی این تابع را مسدود نمی‌کند، چندین فراخوانی پشت‌سرهم
    (مثلاً ۵ بار) باعث ۵ اجرای کاملاً هم‌زمان می‌شود.
    """
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(_run_job(job_id), loop)
    with _futures_lock:
        _futures[job_id] = fut


def cancel_job(job_id: str) -> bool:
    """
    اگر job در حال اجراست، تلاش می‌کند فوراً متوقفش کند — کنسل‌کردن
    Future باعث می‌شود CancelledError در نقطهٔ await بعدی داخل
    _search_one (که نقاط await فراوانی دارد) raise شود، و بلوک
    finally که مرورگر را می‌بندد (ctx.close()) بلافاصله اجرا می‌شود.
    اگر job در صف یا تازه شروع‌شده باشد هم همین مسیر کار می‌کند.
    """
    with _futures_lock:
        fut = _futures.pop(job_id, None)
    if fut is not None:
        fut.cancel()
        return True
    return False
