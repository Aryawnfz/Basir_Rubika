"""بصیر – Basir Flask Application"""
import asyncio
import json
import os
import time
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
import rubika_explore
import explore_store
import explore_runner
from rubika_search import search_all_accounts, profile_has_data
from explore_export import generate_channel_stats_excel, generate_all_channels_excel
from export import generate_excel
from stats_export import generate_stats_excel, _compute as _compute_stats, _fmt_int, _fmt_float
from report_export import generate_report_excel
from text_highlight import highlight_query, card_excerpt

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.jinja_env.globals["highlight_query"] = highlight_query
app.jinja_env.globals["card_excerpt"] = card_excerpt


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


def _period_cutoff(period: str) -> float:
    """مرزِ زمانیِ بازهٔ 'day' | 'week' | 'month'؛ برای بازهٔ نامعتبر/خالی صفر."""
    now = time.time()
    return {
        "day":   now - 86_400,
        "week":  now - 7 * 86_400,
        "month": now - 30 * 86_400,
    }.get(period, 0)


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

    «priority»: جایگاهِ واقعی و سراسریِ هر جستجوی هنوز تمام‌نشده (queued/
    running) در بینِ همهٔ جستجوهای هنوز تمام‌نشدهٔ کل سامانه — روی همهٔ
    کاربران، نه فقط همان کاربر. این عمداً روی all_jobs (قبل از فیلترِ
    دسترسیِ کاربر) محاسبه می‌شود؛ وگرنه کاربرِ عادی چون جستجوهای بقیه را
    نمی‌بیند، فکر می‌کرد جستجویش همیشه اولویتِ اول است.

    «server_time» هم همراهِ لیست برگردانده می‌شود تا کلاینت بتواند اختلافِ
    احتمالیِ ساعتِ سرور با ساعتِ واقعیِ کاربر را جبران کند (مثلاً اگر ساعتِ
    سیستمِ سرور درست تنظیم نشده باشد) — تا «زمان ثبت» همیشه بر اساسِ ساعتِ
    واقعیِ همین لحظه نمایش داده شود، نه ساعتِ اشتباهِ سرور.
    """
    all_jobs = jobs.list_jobs()

    in_progress = [j for j in all_jobs if j.get("status") in (jobs.ST_QUEUED, jobs.ST_RUNNING)]
    in_progress.sort(key=lambda j: j.get("created_at", 0))  # قدیمی‌ترین = اولویتِ ۱
    priority_by_id = {j.get("id"): i + 1 for i, j in enumerate(in_progress)}

    visible_jobs = all_jobs
    if not _is_admin():
        visible_jobs = [j for j in all_jobs if j.get("created_by") == session.get("user")]

    # فیلترِ بازهٔ زمانیِ لیست (روز/هفته/ماه). اولویتِ صف روی همهٔ jobها
    # (قبل از این فیلتر) حساب شده، پس این فیلتر آن را به‌هم نمی‌زند.
    cutoff = _period_cutoff(request.args.get("period", "").strip())
    if cutoff:
        visible_jobs = [j for j in visible_jobs if j.get("created_at", 0) >= cutoff]

    summary = []
    for j in visible_jobs:
        row = {k: v for k, v in j.items() if k != "results"}
        row["priority"] = priority_by_id.get(j.get("id"))
        summary.append(row)

    return jsonify({"jobs": summary, "server_time": time.time()})


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
    all_results = job.get("results", [])
    s = _compute_stats(all_results)
    quick_stats = {
        "n": _fmt_int(s["n"]),
        "total_views": _fmt_int(s["total_views"]),
        "total_reactions": _fmt_int(s["total_reactions"]),
        "pos": _fmt_int(s["pos"]),
        "neg": _fmt_int(s["neg"]),
        "neu": _fmt_int(s["neu"]),
        "avg_views": _fmt_float(s["avg_views"]),
        "avg_reactions": _fmt_float(s["avg_reactions"]),
        "engagement": f"{s['engagement']:.1f}%",
    }
    return render_template(
        "results.html",
        job=job,
        results=all_results,
        query=job.get("query", ""),
        date_from=job.get("date_from", ""),
        date_to=job.get("date_to", ""),
        quick_stats=quick_stats,
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


# ── کاوش کانال‌ها ──────────────────────────────────────────────────────────────
@app.route("/explore")
@login_required
def explore_page():
    return render_template(
        "explore.html",
        channels=explore_store.list_channels(),
    )


@app.route("/explore/add", methods=["POST"])
@login_required
def explore_add():
    raw = (request.form.get("channel") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "لطفاً لینک یا آیدی کانال را وارد کنید."}), 400
    try:
        info = rubika_explore.run_sync(rubika_explore.fetch_channel_info(raw))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"خطا در دریافت اطلاعات کانال: {exc}"}), 500
    if not info:
        return jsonify({"ok": False, "error": "کانالی با این لینک/آیدی پیدا نشد."}), 404
    ok, msg, record = explore_store.add_channel(session.get("user", ""), info)
    return jsonify({"ok": ok, "message": msg, "channel": record}), (200 if ok else 409)


@app.route("/explore/add_bulk", methods=["POST"])
@login_required
def explore_add_bulk():
    raw = request.form.get("channels") or ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # حذفِ ورودی‌های تکراری با حفظِ ترتیب
    seen = set()
    lines = [ln for ln in lines if not (ln in seen or seen.add(ln))]
    if not lines:
        return jsonify({"ok": False, "error": "لطفاً حداقل یک لینک یا آیدی کانال وارد کنید."}), 400
    try:
        infos = rubika_explore.run_sync(rubika_explore.fetch_channel_info_bulk(lines))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"خطا در دریافت اطلاعات کانال‌ها: {exc}"}), 500

    owner = session.get("user", "")
    items = []
    added = failed = duplicate = 0
    for res in infos:
        entry = {"raw": res["raw"]}
        if res.get("info"):
            ok, msg, record = explore_store.add_channel(owner, res["info"])
            entry["ok"] = ok
            entry["message"] = msg
            entry["channel"] = record
            if ok:
                added += 1
            else:
                duplicate += 1
        else:
            entry["ok"] = False
            entry["message"] = res.get("error") or "کانالی با این لینک/آیدی پیدا نشد."
            entry["channel"] = None
            failed += 1
        items.append(entry)
    return jsonify({"ok": True, "added": added, "duplicate": duplicate,
                    "failed": failed, "items": items})


@app.route("/explore/delete/<channel_id>", methods=["POST"])
@login_required
def explore_delete(channel_id):
    ok = explore_store.delete_channel(channel_id)
    if ok:
        explore_runner.delete_analysis(channel_id)
    return jsonify({"ok": ok})


@app.route("/explore/delete_all", methods=["POST"])
@login_required
def explore_delete_all():
    """حذفِ همهٔ کانال‌های کاوش (سراسری)."""
    count = explore_store.delete_all_channels()
    explore_runner.clear_all()
    return jsonify({"ok": True, "count": count})


@app.route("/explore/refresh_all", methods=["POST"])
@login_required
def explore_refresh_all():
    """
    اطلاعاتِ نمایشیِ همهٔ کانال‌ها (عنوان/بیو/تعدادِ مشترکان/آواتار) را دوباره از
    وب‌اپِ روبیکا می‌خواند و به‌روز می‌کند — درست مثل اینکه همین الان دوباره
    اضافه شده باشند.
    """
    channels = explore_store.list_channels()
    if not channels:
        return jsonify({"ok": False, "error": "هیچ کانالی برای به‌روزرسانی وجود ندارد."}), 400
    raws = [c.get("username") or c.get("url") or "" for c in channels]
    try:
        infos = rubika_explore.run_sync(rubika_explore.fetch_channel_info_bulk(raws))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"خطا در به‌روزرسانیِ اطلاعات: {exc}"}), 500

    items = []
    updated = failed = 0
    for ch, res in zip(channels, infos):
        if res.get("info"):
            record = explore_store.update_channel(ch["id"], res["info"])
            items.append({"id": ch["id"], "ok": True, "channel": record})
            updated += 1
        else:
            items.append({"id": ch["id"], "ok": False,
                          "error": res.get("error") or "اطلاعاتِ تازه پیدا نشد."})
            failed += 1
    return jsonify({"ok": True, "updated": updated, "failed": failed, "items": items})


@app.route("/explore/analyze", methods=["POST"])
@login_required
def explore_analyze():
    """بررسیِ همهٔ کانال‌ها با همان تعدادِ پست (سراسری و یکپارچه)."""
    try:
        num_posts = int(request.form.get("num_posts", "10"))
    except (TypeError, ValueError):
        num_posts = 10
    num_posts = max(1, min(config.EXPLORE_MAX_POSTS, num_posts))
    channels = explore_store.list_channels()
    if not channels:
        return jsonify({"ok": False, "error": "هیچ کانالی برای بررسی وجود ندارد."}), 400
    explore_runner.start_batch(channels, num_posts, session.get("user", ""))
    return jsonify({"ok": True, "count": len(channels), "num_posts": num_posts})


@app.route("/explore/status")
@login_required
def explore_status():
    """وضعیتِ بررسیِ همهٔ کانال‌ها (سراسری)."""
    return jsonify({"ok": True, "channels": explore_runner.all_status()})


@app.route("/explore/analysis/<channel_id>")
@login_required
def explore_analysis(channel_id):
    """نتیجهٔ کاملِ بررسیِ یک کانال (برای مودالِ نمایش)."""
    item = explore_runner.get_analysis(channel_id)
    if not item:
        return jsonify({"ok": False, "error": "یافت نشد."}), 404
    return jsonify({
        "ok": True,
        "status": item.get("status", ""),
        "result": item.get("result"),
        "error": item.get("error", ""),
    })


@app.route("/explore/export/<channel_id>")
@login_required
def explore_export(channel_id):
    item = explore_runner.get_analysis(channel_id)
    result = (item or {}).get("result")
    if not item or item.get("status") != explore_runner.ST_DONE or not result:
        flash("آمار هنوز آماده نیست.", "warning")
        return redirect(url_for("explore_page"))
    return send_file(
        generate_channel_stats_excel(result),
        as_attachment=True,
        download_name=f"basir_channel_{result.get('username','')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/explore/export_all")
@login_required
def explore_export_all():
    current_ids = {c["id"] for c in explore_store.list_channels()}
    records = [r for r in explore_runner.done_records() if r.get("channel_id") in current_ids]
    if not records:
        flash("هنوز آمارِ آماده‌ای برای خروجی وجود ندارد.", "warning")
        return redirect(url_for("explore_page"))
    return send_file(
        generate_all_channels_excel(records),
        as_attachment=True,
        download_name="basir_channels_stats.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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


@app.route("/users/edit/<username>", methods=["POST"])
@admin_required
def user_edit(username):
    new_username = request.form.get("username", "").strip()
    new_password = request.form.get("password", "").strip()
    ok, msg = user_store.update_user(username, new_username, new_password or None)
    if ok and new_username and new_username != username:
        jobs.rename_owner(username, new_username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("users_page"))


# ── Accounts ───────────────────────────────────────────────────────────────────
def _build_report(period: str, usernames: list[str] | None = None) -> dict:
    """
    محاسبهٔ کاملِ گزارشِ یک بازه — منبعِ واحدِ صفحهٔ /reports و خروجی اکسلِ آن،
    تا عددهای صفحه و فایل همیشه دقیقاً یکی باشند.

    usernames: اگر داده شود، گزارش فقط شاملِ جستجوهایی می‌شود که created_by
    آن‌ها در این لیست باشد (فیلترِ «کاربر» — برای کاربرِ عادی همیشه فقط نامِ
    خودش، برای مدیر هر ترکیبی که از Combo Box بالای صفحه انتخاب کند؛ خالی/
    None یعنی بدونِ فیلتر = همهٔ کاربران).
    """
    import time as _time
    from collections import defaultdict

    period = (period or "").strip()   # '' | 'day' | 'week' | 'month'

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

    # فیلترِ کاربر: کاربرِ عادی فقط جستجوهای خودش، مدیر هر ترکیبی که از
    # Combo Box انتخاب کند (خالی/None یعنی بدونِ فیلتر = همهٔ کاربران).
    if usernames:
        allowed = set(usernames)
        filtered = [r for r in filtered if r.get("created_by", "") in allowed]

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

    # ── داده‌ی نمودار ستونی: پرتکرارترین جستجوها (۸ تای اول) ────────────────
    top_queries = sorted_queries[:8]
    max_query_count = max((c["count"] for _, c in top_queries), default=0)
    bar_chart = [
        {
            "query": q,
            "count": c["count"],
            "pct": round((c["count"] / max_query_count) * 100, 1) if max_query_count else 0,
        }
        for q, c in top_queries
    ]

    # ── داده‌ی نمودار دایره‌ای: سهمِ هر اکانت از کلِ پیام‌های پیداشده ─────────
    _CHART_COLORS = [
        "#12a4ff", "#f0c040", "#22d3ee", "#10b981", "#0d84cf",
        "#f87171", "#5ec4ff", "#a78bfa", "#6ee7b7", "#eab308",
    ]
    account_totals: dict[str, int] = defaultdict(int)
    for r in filtered:
        for name, cnt in (r.get("account_counts") or {}).items():
            account_totals[name] += cnt

    total_account_msgs = sum(account_totals.values())
    sorted_accounts = sorted(account_totals.items(), key=lambda kv: -kv[1])

    account_slices = []
    cursor = 0.0
    for i, (name, cnt) in enumerate(sorted_accounts):
        pct = (cnt / total_account_msgs * 100) if total_account_msgs else 0
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        account_slices.append({
            "name": name,
            "count": cnt,
            "pct": round(pct, 1),
            "color": color,
            "start": round(cursor, 3),
            "end": round(cursor + pct, 3),
        })
        cursor += pct

    if account_slices:
        pie_gradient = ", ".join(
            f"{s['color']} {s['start']}% {s['end']}%" for s in account_slices
        )
    else:
        pie_gradient = "var(--bg-surface) 0% 100%"

    return {
        "queries": sorted_queries,
        "period": period,
        "total_searches": total_searches,
        "total_results": total_results,
        "server_time": now,
        "bar_chart": bar_chart,
        "account_slices": account_slices,
        "pie_gradient": pie_gradient,
        "total_account_msgs": total_account_msgs,
    }


def _report_usernames() -> tuple[list[str] | None, list[str], list[str]]:
    """
    فیلترِ کاربر برای گزارش را برمی‌گرداند: (usernames_for_filter, selected,
    all_usernames). کاربرِ عادی همیشه فقط نامِ خودش را می‌بیند (و Combo Box
    اصلاً نشانش داده نمی‌شود)؛ مدیر می‌تواند از Combo Box چند کاربر را
    هم‌زمان انتخاب کند — انتخابِ خالی یعنی همهٔ کاربران.
    """
    if _is_admin():
        all_usernames = [u.get("username", "") for u in user_store.load_users()]
        selected = [u for u in request.args.getlist("users") if u in all_usernames]
        return (selected or None), selected, all_usernames
    me = session.get("user", "")
    return [me], [me], []


@app.route("/reports")
@login_required
def reports():
    period = request.args.get("period", "").strip()   # '' | 'day' | 'week' | 'month'
    usernames, selected_users, all_usernames = _report_usernames()
    report = _build_report(period, usernames)
    return render_template(
        "reports.html",
        **report,
        is_admin=_is_admin(),
        selected_users=selected_users,
        all_usernames=all_usernames,
    )


@app.route("/reports/export")
@login_required
def reports_export():
    """همان گزارشِ فیلترشدهٔ صفحه (بازه + کاربر)، به‌صورت فایل اکسل."""
    period = request.args.get("period", "").strip()
    usernames, _selected, _all = _report_usernames()
    report = _build_report(period, usernames)
    suffix = period or "all"
    return send_file(
        generate_report_excel(report),
        as_attachment=True,
        download_name=f"basir_report_{suffix}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    app.run(debug=True, host="0.0.0.0", port=5001)