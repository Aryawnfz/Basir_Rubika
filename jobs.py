"""
انباره Jobهای جستجوی بصیر — هر job یک درخواست جستجوی مستقل است.

این ماژول صرفاً وضعیت هر job را روی دیسک نگه می‌دارد (file-based، شبیه
الگوی login_manager.py). فعلاً هیچ UI برای نمایش این لیست ساخته نشده —
طبق درخواست کاربر، فقط زیرساخت اجرای هم‌زمان (concurrent) ساخته می‌شود؛
نحوهٔ نمایش نتایج بعداً مشخص خواهد شد.

ساختار فایل: data/jobs/<job_id>.json

── لاگ دائمی گزارش (search_log) ──────────────────────────────────────────────
لیست jobها (data/jobs/*.json) «کاری» است: کاربر می‌تواند هر job را از صفحهٔ
اصلی حذف کند تا جدول تمیز بماند. اما گزارش (/reports) باید همیشگی و
غیرقابل‌حذف باشد و آمار واقعی همهٔ جستجوهای ثبت‌شده را نشان دهد — صرف‌نظر
از اینکه job مربوطه بعداً از لیست حذف شده باشد یا نه.

برای همین، جدا از فایل هر job، یک رکورد append-only هم در
data/search_log.jsonl نوشته می‌شود (هر خط = یک JSON مستقل). این فایل هرگز
توسط delete_job لمس نمی‌شود، پس گزارش همیشه کامل می‌ماند.
"""
import json
import os
import time
import uuid

from config import DATA_DIR

JOBS_DIR             = os.path.join(DATA_DIR, "jobs")
SEARCH_LOG_FILE       = os.path.join(DATA_DIR, "search_log.jsonl")
SEARCH_LOG_RESULTS_FILE = os.path.join(DATA_DIR, "search_log_results.json")
SEARCH_LOG_ACCOUNTS_FILE = os.path.join(DATA_DIR, "search_log_accounts.json")

ST_QUEUED  = "queued"
ST_RUNNING = "running"
ST_DONE    = "done"
ST_ERROR   = "error"


def _ensure_dir():
    os.makedirs(JOBS_DIR, exist_ok=True)


def _job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def _write_job(job: dict) -> None:
    """نوشتن atomic — جلوگیری از خوانده‌شدن فایل نیمه‌نوشته."""
    _ensure_dir()
    path = _job_path(job["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    os.replace(tmp, path)


def _append_search_log(job: dict) -> None:
    """
    افزودن یک رکورد دائمی به لاگ گزارش. append-only — هیچ تابعی این فایل
    را پاک یا بازنویسی نمی‌کند، پس با حذف job از لیست صفحهٔ اصلی، این
    رکورد دست‌نخورده باقی می‌ماند و در گزارش باقی می‌ماند.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    record = {
        "job_id":     job["id"],
        "query":      job["query"],
        "created_by": job.get("created_by", ""),
        "created_at": job.get("created_at", time.time()),
    }
    with open(SEARCH_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_search_log_results() -> dict:
    """دیکشنری {job_id: result_count} برای jobهای تکمیل‌شده — دائمی و مستقل از حذف job."""
    if not os.path.exists(SEARCH_LOG_RESULTS_FILE):
        return {}
    try:
        with open(SEARCH_LOG_RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _set_search_log_result_count(job_id: str, result_count: int) -> None:
    """
    ثبت دائمی تعداد نتایج یک job در لاگ گزارش — صدا زده می‌شود وقتی job
    به نتیجه می‌رسد (result_count تغییر می‌کند)، نه فقط لحظهٔ ایجاد.
    این فایل هم مثل search_log.jsonl هرگز توسط delete_job پاک نمی‌شود.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    data = _read_search_log_results()
    data[job_id] = result_count
    tmp = SEARCH_LOG_RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, SEARCH_LOG_RESULTS_FILE)


def _read_search_log_accounts() -> dict:
    """دیکشنری {job_id: {account_name: count}} — دائمی و مستقل از حذف job."""
    if not os.path.exists(SEARCH_LOG_ACCOUNTS_FILE):
        return {}
    try:
        with open(SEARCH_LOG_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _set_search_log_account_counts(job_id: str, results: list[dict]) -> None:
    """
    ثبت دائمیِ سهمِ هر اکانت از نتایجِ این job — برای نمودارِ «سهم مشارکت
    اکانت‌ها» در گزارش. مثلِ result_count، این هم فقط لحظه‌ی تکمیل‌شدنِ job
    ثبت می‌شود (نه با حذفِ تک‌تکِ نتایج بعداً) تا با همان فلسفه‌ی
    search_log_results.json هماهنگ بماند.
    """
    counts: dict[str, int] = {}
    for r in (results or []):
        name = (r.get("account_name") or "نامشخص").strip() or "نامشخص"
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    data = _read_search_log_accounts()
    data[job_id] = counts
    tmp = SEARCH_LOG_ACCOUNTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, SEARCH_LOG_ACCOUNTS_FILE)


def _migrate_existing_jobs_to_log() -> None:
    """
    Migration یک‌باره: اگر search_log.jsonl هنوز وجود ندارد ولی jobهای
    قدیمی‌تر (از قبل از این تغییر) در data/jobs/ هستند، آن‌ها را هم به
    لاگ دائمی اضافه می‌کند — تا گزارش جستجوهای قبلی که هنوز حذف نشده‌اند
    از بین نرود. بعد از اولین اجرا، چون فایل لاگ دیگر وجود دارد، این
    تابع کاری انجام نمی‌دهد.
    """
    if os.path.exists(SEARCH_LOG_FILE):
        return
    _ensure_dir()
    existing = [f for f in os.listdir(JOBS_DIR) if f.endswith(".json")]
    if not existing:
        return
    old_jobs = []
    for fname in existing:
        try:
            with open(os.path.join(JOBS_DIR, fname), "r", encoding="utf-8") as f:
                old_jobs.append(json.load(f))
        except Exception:
            continue
    old_jobs.sort(key=lambda j: j.get("created_at", 0))
    os.makedirs(DATA_DIR, exist_ok=True)
    result_counts = _read_search_log_results()
    account_counts = _read_search_log_accounts()
    with open(SEARCH_LOG_FILE, "a", encoding="utf-8") as f:
        for job in old_jobs:
            record = {
                "job_id":     job.get("id", ""),
                "query":      job.get("query", ""),
                "created_by": job.get("created_by", ""),
                "created_at": job.get("created_at", 0),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if job.get("id"):
                result_counts[job["id"]] = job.get("result_count", 0)
                counts: dict[str, int] = {}
                for r in (job.get("results") or []):
                    name = (r.get("account_name") or "نامشخص").strip() or "نامشخص"
                    counts[name] = counts.get(name, 0) + 1
                if counts:
                    account_counts[job["id"]] = counts
    if result_counts:
        tmp = SEARCH_LOG_RESULTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result_counts, f, ensure_ascii=False)
        os.replace(tmp, SEARCH_LOG_RESULTS_FILE)
    if account_counts:
        tmp = SEARCH_LOG_ACCOUNTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(account_counts, f, ensure_ascii=False)
        os.replace(tmp, SEARCH_LOG_ACCOUNTS_FILE)


def _migrate_missing_account_counts() -> None:
    """
    Migration مکمل: برخلافِ _migrate_existing_jobs_to_log که فقط یک‌بار (وقتی
    search_log.jsonl اصلاً وجود نداشت) اجرا می‌شود، این تابع هر بار بررسی
    می‌کند که آیا رکوردی در لاگ هست که هنوز در search_log_accounts.json سهمِ
    اکانت‌هایش ثبت نشده — مثلاً چون آن job قبل از اضافه‌شدنِ این قابلیت
    تکمیل شده بود. اگر فایلِ خودِ job هنوز در data/jobs/ موجود باشد (حذف
    نشده باشد)، سهمِ اکانت‌ها از رویش محاسبه و برای همیشه ثبت می‌شود. برای
    jobهایی که قبلاً حذف شده‌اند، دیگر داده‌ای برای بازیابی وجود ندارد.
    """
    if not os.path.exists(SEARCH_LOG_FILE):
        return
    account_counts = _read_search_log_accounts()
    job_ids: list[str] = []
    with open(SEARCH_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            jid = rec.get("job_id", "")
            if jid:
                job_ids.append(jid)

    changed = False
    for jid in job_ids:
        if jid in account_counts:
            continue
        job = get_job(jid)
        if not job:
            continue
        counts: dict[str, int] = {}
        for r in (job.get("results") or []):
            name = (r.get("account_name") or "نامشخص").strip() or "نامشخص"
            counts[name] = counts.get(name, 0) + 1
        if counts:
            account_counts[jid] = counts
            changed = True

    if changed:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SEARCH_LOG_ACCOUNTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(account_counts, f, ensure_ascii=False)
        os.replace(tmp, SEARCH_LOG_ACCOUNTS_FILE)


def list_search_log(limit: int = 0) -> list[dict]:
    """
    تمام رکوردهای دائمی جستجو را برمی‌گرداند — مستقل از اینکه job مربوطه
    هنوز در data/jobs/ موجود است یا حذف شده. منبع داده‌ی گزارش (/reports)
    دقیقاً همین تابع است، نه list_jobs().

    هر رکورد شامل result_count هم می‌شود (از روی فایل دائمی
    search_log_results.json) — اگر job هنوز تکمیل نشده یا نتیجه‌اش هنوز
    ثبت نشده باشد، result_count برابر 0 خواهد بود.

    قدیمی‌ترین رکوردها اول برگردانده می‌شوند (هم‌راستا با list_jobs).
    اگر limit>0 باشد، فقط `limit` رکورد آخر (جدیدترین‌ها) برگردانده می‌شود.
    """
    _migrate_existing_jobs_to_log()
    _migrate_missing_account_counts()
    if not os.path.exists(SEARCH_LOG_FILE):
        return []
    result_counts = _read_search_log_results()
    account_counts = _read_search_log_accounts()
    records = []
    with open(SEARCH_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["result_count"] = result_counts.get(rec.get("job_id", ""), 0)
            rec["account_counts"] = account_counts.get(rec.get("job_id", ""), {})
            records.append(rec)
    return records[-limit:] if limit else records


def create_job(query: str, date_from: str = "", date_to: str = "",
                created_by: str = "") -> dict:
    job = {
        "id":           str(uuid.uuid4()),
        "query":        query,
        "date_from":    date_from,
        "date_to":      date_to,
        "status":       ST_QUEUED,
        "message":      "در صف اجرا...",
        "error":        "",
        "result_count": 0,
        "results":      [],
        "created_by":   created_by,
        "created_at":   time.time(),
    }
    _write_job(job)
    # ثبت دائمی در لاگ گزارش — همان لحظه‌ای که جستجو ثبت می‌شود، نه بعداً،
    # تا حتی اگر job بلافاصله (مثلاً توسط کاربر) حذف شود هم در گزارش بماند.
    _append_search_log(job)
    return job


def update_job(job_id: str, **fields) -> dict | None:
    job = get_job(job_id)
    if job is None:
        return None
    job.update(fields)
    _write_job(job)
    # اگر تعداد نتایج این job مشخص/تغییر کرده (مثلاً وقتی job تکمیل شد)،
    # آن را در لاگ دائمی هم ثبت کن — تا کادر «کل نتایج» در گزارش حتی بعد
    # از حذف این job از لیست، درست باقی بماند.
    if "result_count" in fields:
        _set_search_log_result_count(job_id, fields.get("result_count") or 0)
    if "results" in fields:
        _set_search_log_account_counts(job_id, fields.get("results") or [])
    return job


def get_job(job_id: str) -> dict | None:
    path = _job_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_jobs(limit: int = 200) -> list[dict]:
    """
    جدیدترین jobها اول — جستجوی تازه‌ثبت‌شده بالای جدولِ صفحه‌ی اصلی نمایش
    داده می‌شود، نه پایینِ آن.
    """
    _ensure_dir()
    all_jobs = []
    for fname in os.listdir(JOBS_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(JOBS_DIR, fname), "r", encoding="utf-8") as f:
                all_jobs.append(json.load(f))
        except Exception:
            continue
    all_jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return all_jobs[:limit] if limit else all_jobs


def remove_result(job_id: str, message_link: str = "", index: int = -1) -> bool:
    """
    حذفِ یک نتیجهٔ منفرد از آرایهٔ results یک job. اولویت با تطبیقِ
    message_link (یکتا) است؛ اگر لینک خالی بود از index استفاده می‌شود.
    پس از حذف، result_count هم به‌روز می‌شود تا آمار/خروجی اکسل همان لیستِ
    باقی‌مانده را ببینند.

    نکته: این حذف عمداً لاگِ دائمیِ گزارش (search_log_results.json) را
    دست نمی‌زند؛ پس تعدادِ نتایج در «گزارش فعالیت‌ها» بدونِ تغییر می‌ماند و
    فقط لیستِ نتایج، خروجی اکسل و «آمار کلی» به‌روز می‌شوند (چون این‌ها از
    job["results"] می‌خوانند). برای همین به‌جای update_job مستقیماً فایلِ
    job نوشته می‌شود تا _set_search_log_result_count صدا زده نشود.
    """
    job = get_job(job_id)
    if job is None:
        return False
    results = job.get("results", [])
    new_results = None
    if message_link:
        new_results = [r for r in results if r.get("message_link") != message_link]
    elif 0 <= index < len(results):
        new_results = [r for i, r in enumerate(results) if i != index]
    if new_results is None or len(new_results) == len(results):
        return False
    job["results"] = new_results
    job["result_count"] = len(new_results)
    _write_job(job)
    return True


def delete_job(job_id: str) -> bool:
    path = _job_path(job_id)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False


def rename_owner(old_username: str, new_username: str) -> int:
    """
    وقتی مدیر نامِ کاربریِ یک کاربر را تغییر می‌دهد، مالکیتِ jobهای قبلیِ او
    (created_by) هم به‌روزرسانی می‌شود — تا کاربر بعد از تغییرِ نام، دسترسی
    به جستجوهای قبلیِ خودش را از دست ندهد. تعدادِ jobهای به‌روزشده را
    برمی‌گرداند.
    """
    _ensure_dir()
    count = 0
    for fname in os.listdir(JOBS_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(JOBS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                job = json.load(f)
        except Exception:
            continue
        if job.get("created_by") == old_username:
            job["created_by"] = new_username
            _write_job(job)
            count += 1
    return count
