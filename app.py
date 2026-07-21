"""بصیر – Basir Flask Application"""
import asyncio
import json
import os
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, send_file, flash, jsonify
)

import config
import accounts as acc_store
import users as user_store
import login_manager
import jobs
import job_runner
from rubika_search import search_all_accounts, profile_has_data
from export import generate_excel
from stats_export import generate_stats_excel

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


# ── helpers ────────────────────────────────────────────────────────────────────
def _enrich(accounts):
    for a in accounts:
        a["profile_ready"] = profile_has_data(a.get("user_data_dir", ""))
    return accounts

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """فقط کاربرانِ نقش admin — کاربرِ عادی به این مسیرها دسترسی ندارد."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if session.get("role") != user_store.ROLE_ADMIN:
            if request.accept_mimetypes.best == "application/json" or request.path.startswith("/api"):
                return jsonify({"ok": False, "error": "دسترسی غیرمجاز."}), 403
            flash("این بخش فقط برای مدیر در دسترس است.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_role():
    """در دسترس قرار دادنِ نقشِ کاربر برای همهٔ قالب‌ها (برای نمایش/مخفی‌کردن منو)."""
    return {"current_role": session.get("role", ""), "ROLE_ADMIN": user_store.ROLE_ADMIN}


def _is_admin() -> bool:
    return session.get("role") == user_store.ROLE_ADMIN


def _owns_job(job: dict) -> bool:
    """
    مالکیتِ یک job: کاربرِ عادی فقط جستجوهایی را می‌بیند/حذف می‌کند که خودش
    ثبت کرده (created_by == نام‌کاربری session). فقط مدیرِ اصلی به همهٔ
    جستجوها دسترسیِ کامل دارد.
    """
    if _is_admin():
        return True
    return job.get("created_by") == session.get("user")

def _save_results(results):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.RESULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

def _load_results():
    if not os.path.exists(config.RESULTS_FILE):
        return []
    with open(config.RESULTS_FILE, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []
 
# ── Auth ───────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u  = request.form.get("username", "").strip()
        pw = request.form.get("password", "").strip()
        account = user_store.verify(u, pw)
        if account:
            session["user"] = account["username"]
            # فقط حسابِ مدیرِ اصلی نقشِ admin دارد؛ بقیه همیشه «عادی» هستند.
            session["role"] = (user_store.ROLE_ADMIN
                               if user_store.is_admin_username(account["username"])
                               else user_store.ROLE_USER)
            return redirect(url_for("index"))
        error = "نام کاربری یا رمز عبور اشتباه است."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Main ───────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html", accounts=_enrich(acc_store.load_accounts()))

@app.route("/search", methods=["POST"])
@login_required
def search():
    query     = request.form.get("query", "").strip()
    date_from = request.form.get("date_from", "").strip()
    date_to   = request.form.get("date_to", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "لطفاً یک عبارت برای جستجو وارد کنید."}), 400
    ready = [a for a in acc_store.load_accounts()
             if profile_has_data(a.get("user_data_dir", ""))]
    if not ready:
        return jsonify({"ok": False, "error": "هیچ اکانت آماده‌ای وجود ندارد."}), 400

    # هر کلیک روی جستجو یک job مستقل می‌سازد که بلافاصله و به‌صورت
    # هم‌زمان (concurrent) با سایر jobها در پس‌زمینه اجرا می‌شود — این
    # درخواست HTTP منتظر اتمام جستجو نمی‌ماند و فوراً برمی‌گردد.
    job = jobs.create_job(query, date_from, date_to, created_by=session.get("user", ""))
    job_runner.start_job(job["id"])
    return jsonify({"ok": True, "job": job})


@app.route("/jobs")
@login_required
def jobs_list():
    """
    Polling endpoint برای جدول لیست جستجوها — بدون آرایهٔ results (سبک).
    کاربرِ عادی فقط جستجوهای خودش را می‌بیند؛ فقط مدیرِ اصلی همهٔ جستجوها
    را می‌بیند.
    """
    all_jobs = jobs.list_jobs()
    if not _is_admin():
        all_jobs = [j for j in all_jobs if j.get("created_by") == session.get("user")]
    summary = [{k: v for k, v in j.items() if k != "results"} for j in all_jobs]
    return jsonify(summary)


@app.route("/jobs/<job_id>/results")
@login_required
def job_results(job_id):
    job = jobs.get_job(job_id)
    if not job:
        flash("این جستجو دیگر در لیست موجود نیست.", "error")
        return redirect(url_for("index"))
    if not _owns_job(job):
        flash("شما به این جستجو دسترسی ندارید.", "error")
        return redirect(url_for("index"))
    if job["status"] != jobs.ST_DONE:
        flash("نتایج این جستجو هنوز آماده نیست.", "warning")
        return redirect(url_for("index"))
    return render_template(
        "results.html",
        job=job,
        results=job.get("results", []),
        query=job.get("query", ""),
        date_from=job.get("date_from", ""),
        date_to=job.get("date_to", ""),
    )


@app.route("/jobs/<job_id>/export")
@login_required
def job_export(job_id):
    job = jobs.get_job(job_id)
    if not job or not job.get("results"):
        flash("نتیجه‌ای برای خروجی وجود ندارد.", "warning")
        return redirect(url_for("index"))
    if not _owns_job(job):
        flash("شما به این جستجو دسترسی ندارید.", "error")
        return redirect(url_for("index"))
    return send_file(
        generate_excel(job["results"]),
        as_attachment=True,
        download_name=f"basir_{job['id'][:8]}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/jobs/<job_id>/stats-export")
@login_required
def job_stats_export(job_id):
    job = jobs.get_job(job_id)
    if not job or not job.get("results"):
        flash("نتیجه‌ای برای آمار وجود ندارد.", "warning")
        return redirect(url_for("index"))
    if not _owns_job(job):
        flash("شما به این جستجو دسترسی ندارید.", "error")
        return redirect(url_for("index"))
    return send_file(
        generate_stats_excel(job["results"], job.get("query", "")),
        as_attachment=True,
        download_name=f"basir_stats_{job['id'][:8]}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/jobs/<job_id>/delete", methods=["POST"])
@login_required
def job_delete(job_id):
    """
    حذف از لیست. اگر job در حال اجراست، ابتدا تلاش می‌کند بلافاصله
    متوقفش کند (مرورگرش بسته شود) و سپس رکورد را از لیست پاک می‌کند.

    کاربرِ عادی فقط جستجوی خودش را می‌تواند حذف کند.
    """
    job = jobs.get_job(job_id)
    if job and not _owns_job(job):
        return jsonify({"ok": False, "error": "شما به این جستجو دسترسی ندارید."}), 403
    job_runner.cancel_job(job_id)
    jobs.delete_job(job_id)
    return jsonify({"ok": True})


@app.route("/results")
@login_required
def results():
    return render_template(
        "results.html",
        results=_load_results(),
        query=session.get("last_query", ""),
        date_from=session.get("last_date_from", ""),
        date_to=session.get("last_date_to", ""),
    )

@app.route("/export")
@login_required
def export():
    data = _load_results()
    if not data:
        flash("نتیجه‌ای برای خروجی وجود ندارد.", "warning")
        return redirect(url_for("results"))
    return send_file(
        generate_excel(data),
        as_attachment=True,
        download_name="basir_results.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/stats-export")
@login_required
def stats_export():
    data = _load_results()
    if not data:
        flash("نتیجه‌ای برای آمار وجود ندارد.", "warning")
        return redirect(url_for("results"))
    return send_file(
        generate_stats_excel(data, session.get("last_query", "")),
        as_attachment=True,
        download_name="basir_stats.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── حذفِ یک نتیجهٔ منفرد از لیست ──────────────────────────────────────────────────
@app.route("/jobs/<job_id>/results/delete", methods=["POST"])
@login_required
def job_result_delete(job_id):
    """
    حذفِ یک پست از نتایجِ یک job — از آمار و خروجی اکسل هم حذف می‌شود.
    کاربرِ عادی فقط می‌تواند از نتایجِ جستجوی خودش حذف کند.
    """
    job = jobs.get_job(job_id)
    if not job:
        return jsonify({"ok": False})
    if not _owns_job(job):
        return jsonify({"ok": False, "error": "شما به این جستجو دسترسی ندارید."}), 403
    link  = (request.form.get("link") or "").strip()
    index = request.form.get("index", "")
    try:
        idx = int(index)
    except (TypeError, ValueError):
        idx = -1
    ok = jobs.remove_result(job_id, message_link=link, index=idx)
    return jsonify({"ok": ok})


@app.route("/results/delete", methods=["POST"])
@login_required
def session_result_delete():
    """حذفِ یک پست از نتایجِ ذخیره‌شدهٔ session (صفحهٔ /results)."""
    link  = (request.form.get("link") or "").strip()
    index = request.form.get("index", "")
    data = _load_results()
    new_data = None
    if link:
        new_data = [r for r in data if r.get("message_link") != link]
    else:
        try:
            idx = int(index)
            if 0 <= idx < len(data):
                new_data = [r for i, r in enumerate(data) if i != idx]
        except (TypeError, ValueError):
            new_data = None
    if new_data is None or len(new_data) == len(data):
        return jsonify({"ok": False})
    _save_results(new_data)
    return jsonify({"ok": True})


# ── مدیریت کاربران (فقط مدیر) ────────────────────────────────────────────────────
@app.route("/users")
@admin_required
def users_page():
    return render_template(
        "users.html",
        users=user_store.load_users(),
        main_admin=config.ADMIN_USER,
    )


@app.route("/users/add", methods=["POST"])
@admin_required
def user_add():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    # همهٔ کاربرانِ افزوده‌شده «عادی» هستند؛ مدیرِ جدید ساخته نمی‌شود.
    ok, msg = user_store.add_user(username, password, user_store.ROLE_USER)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("users_page"))


@app.route("/users/delete/<username>", methods=["POST"])
@admin_required
def user_delete(username):
    ok, msg = user_store.delete_user(username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("users_page"))


# ── Accounts ───────────────────────────────────────────────────────────────────
@app.route("/reports")
@login_required
def reports():
    import time as _time
    from collections import defaultdict

    period = request.args.get("period", "").strip()   # '' | 'day' | 'week' | 'month'

    # بازه زمانی انتخاب‌شده
    now = _time.time()
    cutoffs = {"day": now - 86_400, "week": now - 7 * 86_400, "month": now - 30 * 86_400}
    cutoff = cutoffs.get(period, 0)

    # نکته مهم: گزارش از jobs.list_search_log() می‌خواند، نه از jobs.list_jobs().
    # list_jobs() فقط فایل‌های data/jobs/*.json را برمی‌گرداند که با کلیک روی
    # «حذف» در صفحهٔ اصلی پاک می‌شوند — اگر گزارش از همان منبع بخواند، با حذف
    # یک جستجو از لیست، آمارش از گزارش هم می‌رفت. search_log.jsonl یک لاگ
    # append-only و جداگانه است که هیچ route ای آن را پاک نمی‌کند، پس گزارش
    # همیشه شامل تمام جستجوهای ثبت‌شده می‌ماند — حتی اگر از لیست حذف شده باشند.
    all_records = jobs.list_search_log(limit=1000)
    filtered = [r for r in all_records if r.get("created_at", 0) >= cutoff]

    # ── آمار کلی بازه (دو کادر بالای صفحه) ──────────────────────────────────
    total_searches = len(filtered)
    total_results  = sum(r.get("result_count", 0) for r in filtered)

    freq: dict[str, dict] = defaultdict(lambda: {"count": 0, "last_at": 0})
    for r in filtered:
        q = (r.get("query") or "").strip()
        if not q:
            continue
        freq[q]["count"] += 1
        if r.get("created_at", 0) > freq[q]["last_at"]:
            freq[q]["last_at"] = r["created_at"]

    sorted_queries = sorted(
        freq.items(),
        key=lambda kv: (-kv[1]["count"], -kv[1]["last_at"]),
    )
    return render_template(
        "reports.html",
        queries=sorted_queries,
        period=period,
        total_searches=total_searches,
        total_results=total_results,
    )


@app.route("/accounts")
@admin_required
def accounts_page():
    return render_template("accounts.html", accounts=_enrich(acc_store.load_accounts()))

@app.route("/accounts/add", methods=["POST"])
@admin_required
def account_add():
    name  = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    if not name or not phone:
        flash("نام و شماره تلفن الزامی است.", "error")
        return redirect(url_for("accounts_page"))
    acc_store.add_account(name, phone)
    flash(f"اکانت «{name}» اضافه شد — با «ورود به روبیکا» لاگین کنید.", "success")
    return redirect(url_for("accounts_page"))

@app.route("/accounts/delete/<account_id>", methods=["POST"])
@admin_required
def account_delete(account_id):
    account = acc_store.get_account(account_id)
    name = account["name"] if account else "؟"
    if acc_store.delete_account(account_id):
        login_manager.cleanup(account_id)
        flash(f"اکانت «{name}» و پروفایل آن حذف شد.", "success")
    else:
        flash("اکانت یافت نشد.", "error")
    return redirect(url_for("accounts_page"))


# ── Login flow (headless + OTP from web) ──────────────────────────────────────

@app.route("/accounts/start-login/<account_id>", methods=["POST"])
@admin_required
def account_start_login(account_id):
    """مرحله ۱: Chrome headless رو شروع کن و شماره رو وارد کن."""
    account = acc_store.get_account(account_id)
    if not account:
        return jsonify({"ok": False, "error": "اکانت یافت نشد"}), 404
    login_manager.start_login(account)
    return jsonify({"ok": True})


@app.route("/accounts/login-status/<account_id>")
@admin_required
def account_login_status(account_id):
    """Polling endpoint — وضعیت فعلی login رو برمیگردونه."""
    return jsonify(login_manager.get_status(account_id))


@app.route("/accounts/submit-otp/<account_id>", methods=["POST"])
@admin_required
def account_submit_otp(account_id):
    """مرحله ۲: OTP کاربر رو بگیر و به Playwright inject کن."""
    otp = request.form.get("otp", "").strip()
    if not otp:
        return jsonify({"ok": False, "error": "کد خالی است"}), 400
    ok = login_manager.submit_otp(account_id, otp)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "session پیدا نشد یا منقضی شده"}), 400


@app.route("/accounts/finish-login/<account_id>", methods=["POST"])
@admin_required
def account_finish_login(account_id):
    """وقتی JS تشخیص داد لاگین موفق بود، اکانت رو mark کن."""
    acc_store.mark_logged_in(account_id)
    login_manager.cleanup(account_id)
    return jsonify({"ok": True})


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.PROFILES_DIR, exist_ok=True)
    app.run(debug=True, host="0.0.0.0", port=5000)