"""
اجراکنندهٔ پس‌زمینهٔ «بررسیِ کانال‌ها» در صفحهٔ کاوش.

وضعیتِ بررسی سراسری و ماندگار است: به‌ازای هر کانال یک رکورد روی دیسک
(data/explore_analysis.json) ذخیره می‌شود، پس با رفرش‌شدنِ صفحه یا از هر
مرورگر/حسابِ دیگری همان وضعیت (در حالِ بررسی / آماده) و همان نتیجه دیده
می‌شود. بررسیِ همهٔ کانال‌ها در یک event-loop پس‌زمینه (همان الگوی
job_runner) و به‌صورتِ ترتیبی اجرا می‌شود؛ فرانت‌اند با polling پیش‌رفت را
می‌گیرد.

نکته: فرض بر اجرای Gunicorn با workers=1 است (همان‌طور که در
gunicorn.conf.py تنظیم شده) تا event-loop و قفلِ اکانت یکتا بماند.
"""
import asyncio
import json
import os
import threading
import time

import config
import job_runner
from rubika_explore import analyze_channel

ST_QUEUED = "queued"
ST_RUNNING = "running"
ST_DONE = "done"
ST_ERROR = "error"

ANALYSIS_FILE = os.path.join(config.DATA_DIR, "explore_analysis.json")

_lock = threading.Lock()
_analyses: dict[str, dict] = {}


def _load() -> dict[str, dict]:
    if not os.path.exists(ANALYSIS_FILE):
        return {}
    try:
        with open(ANALYSIS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _flush() -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = ANALYSIS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_analyses, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ANALYSIS_FILE)


def _init() -> None:
    """
    وضعیت‌های ماندگار را بارگذاری می‌کند و هر بررسیِ «در حال اجرا»یی که به‌خاطر
    ری‌استارتِ پروسه نیمه‌کاره مانده را به خطا تبدیل می‌کند (چون دیگر task
    زنده‌ای برایش وجود ندارد).
    """
    global _analyses
    _analyses = _load()
    changed = False
    for rec in _analyses.values():
        if rec.get("status") in (ST_QUEUED, ST_RUNNING):
            rec["status"] = ST_ERROR
            rec["error"] = "بررسی به‌خاطر راه‌اندازی مجدد نیمه‌کاره ماند؛ دوباره اجرا کنید."
            changed = True
    if changed:
        _flush()


_init()


def _set(cid: str, **fields) -> None:
    with _lock:
        rec = _analyses.get(cid, {})
        rec.update(fields)
        _analyses[cid] = rec
        _flush()


async def _run_batch(channels: list[dict], num_posts: int) -> None:
    for ch in channels:
        cid = ch["id"]
        _set(cid, status=ST_RUNNING, started_run_at=time.time())
        try:
            result = await analyze_channel(ch["username"], num_posts)
            _set(cid, status=ST_DONE, result=result, error="",
                 finished_at=time.time())
        except Exception as exc:
            _set(cid, status=ST_ERROR, error=str(exc), finished_at=time.time())


def start_batch(channels: list[dict], num_posts: int, owner: str) -> list[str]:
    """
    بررسیِ همهٔ کانال‌های داده‌شده را با همان تعدادِ پست شروع می‌کند. برای هر
    کانال رکوردِ وضعیت (queued) ساخته و بعد به‌صورتِ ترتیبی اجرا می‌شوند.
    """
    ids = []
    now = time.time()
    for ch in channels:
        cid = ch["id"]
        _set(
            cid,
            channel_id=cid,
            username=ch.get("username", ""),
            title=ch.get("title", ""),
            members=ch.get("members", ""),
            members_count=ch.get("members_count", 0),
            num_posts=num_posts,
            status=ST_QUEUED,
            result=None,
            error="",
            started_by=owner,
            started_at=now,
            finished_at=0,
        )
        ids.append(cid)
    if channels:
        loop = job_runner._ensure_loop()
        asyncio.run_coroutine_threadsafe(_run_batch(list(channels), num_posts), loop)
    return ids


def get_analysis(channel_id: str) -> dict | None:
    with _lock:
        rec = _analyses.get(channel_id)
        return dict(rec) if rec else None


def delete_analysis(channel_id: str) -> None:
    """پاک‌کردنِ رکوردِ بررسیِ یک کانالِ مشخص — وقتی خودِ کانال حذف می‌شود، تا
    نتیجه/آمارِ کانالِ حذف‌شده در خروجیِ کلی باقی نماند."""
    with _lock:
        if channel_id in _analyses:
            del _analyses[channel_id]
            _flush()


def clear_all() -> None:
    """پاک‌کردنِ همهٔ رکوردهای وضعیتِ بررسی — وقتی همهٔ کانال‌ها حذف می‌شوند،
    دیگر نتیجه/وضعیتِ بررسیِ یتیم روی دیسک باقی نمی‌ماند."""
    global _analyses
    with _lock:
        _analyses = {}
        _flush()


def all_status() -> dict[str, dict]:
    """وضعیتِ همهٔ کانال‌ها (سبک‌شده؛ بدون متنِ کاملِ پست‌ها)."""
    out = {}
    with _lock:
        for cid, rec in _analyses.items():
            result = rec.get("result")
            summary = None
            if result:
                summary = {k: v for k, v in result.items() if k != "posts"}
            out[cid] = {
                "status": rec.get("status", ""),
                "num_posts": rec.get("num_posts", 0),
                "error": rec.get("error", ""),
                "summary": summary,
            }
    return out


def done_records() -> list[dict]:
    """رکوردهای بررسیِ کاملاً انجام‌شده (برای خروجی کلی)."""
    with _lock:
        return [dict(r) for r in _analyses.values()
                if r.get("status") == ST_DONE and r.get("result")]
