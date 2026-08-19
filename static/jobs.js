/**
 * ◈ Basir — لیست جستجوها (Jobs Table)  v2
 *
 * رویکرد بدون flash: به‌جای جایگزینی کامل innerHTML با هر poll، فقط
 * سلول‌هایی که واقعاً تغییر کرده‌اند به‌روزرسانی می‌شوند (DOM-diff).
 * ردیف‌های جدید با انیمیشن اضافه می‌شوند؛ ردیف‌های موجود بی‌حرکت می‌مانند.
 */

const POLL_MS = 2000;
let _timer    = null;

// اختلافِ ساعتِ سرور با ساعتِ واقعیِ مرورگر (server_time - Date.now()/1000).
// اگر ساعتِ سیستمِ سرور اشتباه تنظیم شده باشد، این مقدار همان خطاست؛ با کم
// کردنش از هر created_at، به‌جای ساعتِ (احتمالاً اشتباهِ) سرور، ساعتِ واقعیِ
// همین لحظه (بر اساسِ ساعتِ خودِ کاربر) نمایش داده می‌شود.
let _clockSkew = 0;

// بازهٔ زمانیِ انتخاب‌شده برای لیست: '' | 'day' | 'week' | 'month'.
// فیلتر سمتِ سرور اعمال می‌شود؛ اینجا فقط نگه داشته می‌شود تا با رفرش هم باقی بماند.
const PERIOD_KEY = 'basirJobsPeriod';
let _period = '';

// نگه‌داری آخرین وضعیت شناخته‌شده هر job — برای تشخیص تغییر بدون لمس DOM
const _knownStates = new Map(); // job_id → { status, result_count }

// ── تبدیل تاریخ‌ها ──────────────────────────────────────────────────────────

function isoToJalali(isoStr) {
  if (!isoStr) return '';
  try {
    const [gy, gm, gd] = isoStr.split('-').map(Number);
    const j = Jalali.toJalali(gy, gm, gd);
    return Jalali.toStr(j.jy, j.jm, j.jd);
  } catch (e) { return isoStr; }
}

function formatFilter(job) {
  const df = job.date_from, dt = job.date_to;
  if (!df && !dt) return '—';
  const pf = df ? isoToJalali(df) : '';
  const pt = dt ? isoToJalali(dt) : '';
  if (pf && pt) return pf + ' تا ' + pt;
  if (pf)       return 'از ' + pf;
  return 'تا ' + pt;
}

function formatTime(ts) {
  if (!ts) return '—';
  try {
    const adjusted = ts - _clockSkew;   // جبرانِ اختلافِ ساعتِ سرور با ساعتِ واقعی
    const d  = new Date(adjusted * 1000);
    const j  = Jalali.toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate());
    const hh = Jalali.pd(String(d.getHours()).padStart(2, '0'));
    const mm = Jalali.pd(String(d.getMinutes()).padStart(2, '0'));
    return Jalali.toStr(j.jy, j.jm, j.jd) + '\u200c — ' + hh + ':' + mm;
  } catch (e) { return '—'; }
}

// ── ساخت HTML بخش‌های مختلف هر ردیف ────────────────────────────────────────

function buildStatusHTML(job) {
  if (job.status === 'done') {
    return `<span class="job-status job-status-done">✅ تکمیل شد</span>`;
  }
  if (job.status === 'error') {
    const msg = escapeHtml(job.error || 'خطای نامشخص');
    return `<span class="job-status job-status-error" title="${msg}">⚠️ خطا</span>`;
  }
  // priority = جایگاهِ واقعی و سراسریِ این job در بینِ همه‌ی جستجوهای هنوز
  // تمام‌نشده (روی همه‌ی کاربران) — نه فقط بینِ جستجوهای همین کاربر.
  const p = (typeof job.priority === 'number' && job.priority > 0) ? ` (${job.priority})` : '';
  return `<span class="job-status job-status-running"><span class="job-spinner"></span> در حال جستجو${p}</span>`;
}

function buildActionsHTML(job) {
  const done       = job.status === 'done';
  const viewClass  = done ? 'job-btn job-btn-view' : 'job-btn job-btn-view disabled';
  const viewClick  = done ? `onclick="viewJobResults('${job.id}')"` : '';
  return `
    <button class="job-btn job-btn-delete" onclick="deleteJob('${job.id}')">حذف</button>
    <button class="${viewClass}" ${viewClick}>نمایش</button>`;
}

function buildRowHTML(job, rowNum) {
  const title = escapeHtml(job.query);
  const owner = escapeHtml(job.created_by || '—');
  return `
    <div class="jt-col job-row-num">${rowNum}</div>
    <div class="jt-col job-row-title" title="${title}">«${title}»</div>
    <div class="jt-col job-row-user" title="${owner}">${owner}</div>
    <div class="jt-col job-row-filter">${formatFilter(job)}</div>
    <div class="jt-col jt-status-cell">${buildStatusHTML(job)}</div>
    <div class="jt-col job-row-time">${formatTime(job.created_at)}</div>
    <div class="jt-col job-actions jt-actions-cell">${buildActionsHTML(job)}</div>`;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str == null ? '' : String(str);
  return d.innerHTML;
}

// ── DOM-diff: لمس‌نکردن آنچه تغییر نکرده ───────────────────────────────────

function patchTable(newJobs) {
  const body = document.getElementById('jobsTableBody');
  if (!body) return;

  // ── حالت خالی ──────────────────────────────────────────────────────────────
  if (!newJobs || newJobs.length === 0) {
    const msg = _period
      ? 'در این بازهٔ زمانی جستجویی ثبت نشده است.'
      : 'هنوز جستجویی ثبت نشده است.';
    const existingEmpty = document.getElementById('jobsEmpty');
    if (!existingEmpty) {
      body.innerHTML = `<div class="jobs-empty" id="jobsEmpty">${msg}</div>`;
      _knownStates.clear();
    } else if (existingEmpty.textContent !== msg) {
      existingEmpty.textContent = msg;
    }
    return;
  }

  // اگه placeholder خالی بود، حذفش کن
  const emptyEl = document.getElementById('jobsEmpty');
  if (emptyEl) emptyEl.remove();

  const newIds = new Set(newJobs.map(j => j.id));

  // ── حذف ردیف‌هایی که از سرور حذف شده‌اند ────────────────────────────────
  body.querySelectorAll('.job-row[data-job-id]').forEach(row => {
    if (!newIds.has(row.dataset.jobId)) {
      row.remove();
      _knownStates.delete(row.dataset.jobId);
    }
  });

  // ── اضافه/آپدیت ردیف‌ها ──────────────────────────────────────────────────
  newJobs.forEach((job, idx) => {
    const rowNum  = idx + 1;
    const existing = body.querySelector(`.job-row[data-job-id="${job.id}"]`);

    if (!existing) {
      // ردیف جدید — دقیقاً در جایگاهِ idx خودش وارد می‌شود (جدیدترین‌ها بالا)
      const el = document.createElement('div');
      el.className    = 'job-row is-new';
      el.dataset.jobId = job.id;
      el.innerHTML    = buildRowHTML(job, rowNum);
      const anchor = body.children[idx] || null; // live collection: جایگاهِ فعلیِ همون ایندکس
      body.insertBefore(el, anchor);
      // کلاس انیمیشن را بعد از یک فریم حذف کن (animation فقط یک بار)
      requestAnimationFrame(() => {
        setTimeout(() => el.classList.remove('is-new'), 350);
      });
      _knownStates.set(job.id, { status: job.status, result_count: job.result_count, priority: job.priority });
      return;
    }

    // ── شماره ردیف ─────────────────────────────────────────────────────────
    const numEl = existing.querySelector('.job-row-num');
    if (numEl && numEl.textContent !== String(rowNum)) numEl.textContent = rowNum;

    // ── وضعیت و عملیات (فقط اگه تغییر کرده) ─────────────────────────────
    const prev = _knownStates.get(job.id);
    if (!prev || prev.status !== job.status || prev.result_count !== job.result_count
        || prev.priority !== job.priority) {
      const statusEl  = existing.querySelector('.jt-status-cell');
      const actionsEl = existing.querySelector('.jt-actions-cell');
      if (statusEl)  statusEl.innerHTML  = buildStatusHTML(job);
      if (actionsEl) actionsEl.innerHTML = buildActionsHTML(job);
      _knownStates.set(job.id, { status: job.status, result_count: job.result_count, priority: job.priority });
    }
    // عنوان، فیلتر، و زمان ثبت هرگز تغییر نمی‌کنند — لمس نمی‌شوند
  });
}

// ── polling ──────────────────────────────────────────────────────────────────

async function fetchJobs() {
  try {
    const url = _period ? `/jobs?period=${encodeURIComponent(_period)}` : '/jobs';
    const res = await fetch(url, { headers: { 'X-Requested-With': 'fetch' } });
    if (!res.ok) return;
    const data = await res.json();
    if (typeof data.server_time === 'number') {
      _clockSkew = data.server_time - (Date.now() / 1000);
    }
    patchTable(data.jobs || []);
  } catch (e) { /* شکست موقت شبکه — poll بعدی تلاش می‌کند */ }
}

function startPolling() {
  if (_timer) clearInterval(_timer);
  fetchJobs();
  _timer = setInterval(fetchJobs, POLL_MS);
}

// ── عملیات ──────────────────────────────────────────────────────────────────

function renderPeriodButtons() {
  document.querySelectorAll('.jobs-period-btn[data-period]').forEach(btn => {
    if (btn.classList.contains('jobs-period-clear')) return;
    btn.classList.toggle('is-active', btn.dataset.period === _period);
  });
  const clear = document.getElementById('jobsPeriodClear');
  if (clear) clear.style.display = _period ? '' : 'none';
}

function setPeriod(period) {
  _period = period;
  try { localStorage.setItem(PERIOD_KEY, period); } catch (e) { /* بی‌اهمیت */ }
  renderPeriodButtons();
  fetchJobs();
}

function setupPeriodFilter() {
  const box = document.getElementById('jobsPeriodBtns');
  if (!box) return;
  try { _period = localStorage.getItem(PERIOD_KEY) || ''; } catch (e) { _period = ''; }
  if (!['day', 'week', 'month'].includes(_period)) _period = '';
  renderPeriodButtons();

  box.addEventListener('click', (e) => {
    const btn = e.target.closest('.jobs-period-btn[data-period]');
    if (!btn) return;
    // کلیکِ دوباره روی همان بازه، فیلتر را برمی‌دارد
    setPeriod(btn.dataset.period === _period ? '' : btn.dataset.period);
  });
}

function viewJobResults(jobId) {
  window.location.href = '/jobs/' + jobId + '/results';
}

async function deleteJob(jobId) {
  try { await fetch('/jobs/' + jobId + '/delete', { method: 'POST' }); }
  catch (e) { /* poll بعدی وضعیت را اصلاح می‌کند */ }
  fetchJobs();
}

// ── ارسال AJAX (بدون رفرش صفحه) ─────────────────────────────────────────────

function setupSearchForm() {
  const form   = document.getElementById('searchForm');
  const input  = document.getElementById('searchInput');
  const button = document.getElementById('searchButton');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) { input.focus(); return; }

    const orig = button.textContent;
    button.textContent = 'در حال ارسال...';
    button.disabled    = true;

    try {
      const res  = await fetch('/search', { method: 'POST', body: new FormData(form) });
      const data = await res.json();
      if (data.ok) {
        input.value = '';
        input.focus();
        fetchJobs();  // فوری بدون منتظرماندن برای poll
      } else {
        showError(data.error || 'خطایی رخ داد.');
      }
    } catch (err) {
      showError('خطا در ارتباط با سرور.');
    } finally {
      button.textContent = orig;
      button.disabled    = false;
    }
  });
}

function showError(message) {
  let c = document.querySelector('.flash-container');
  if (!c) { c = document.createElement('div'); c.className = 'flash-container'; document.body.appendChild(c); }
  const el = document.createElement('div');
  el.className   = 'flash flash-error';
  el.textContent = '❌ ' + message;
  c.appendChild(el);
  setTimeout(() => {
    el.style.opacity   = '0';
    el.style.transform = 'translateY(-10px)';
    setTimeout(() => el.remove(), 400);
  }, 4000);
}

// ── شروع ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupSearchForm();
  setupPeriodFilter();
  startPolling();
});
