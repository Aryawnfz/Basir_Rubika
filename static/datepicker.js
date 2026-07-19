/**
 * ◈ Basir Persian Date Picker  v3
 * الگوریتم تبدیل: تست‌شده و تأییدشده
 * - پیش‌فرض امروز (درست)
 * - انتخاب سال (grid 3 ستونه)
 * - تبدیل صحیح شمسی ↔ میلادی
 *
 * تست: toJalali(2026,6,10) = 1405/03/20  ✓
 *       toGregorian(1405,3,20) = 2026/06/10  ✓
 */

// ══════════════════════════════════════════════════════════
// کتابخانه جلالی
// ══════════════════════════════════════════════════════════
const Jalali = (() => {
  const _f = Math.floor;

  function toJalali(gy, gm, gd) {
    // کلید: پایه از سال ۱۶۰۰ میلادی = ۹۷۹ شمسی
    const g_y = gy - 1600;
    const g_m = gm - 1;
    const g_d = gd - 1;
    const isLeapG = (gy % 4 === 0) && (gy % 100 !== 0 || gy % 400 === 0);
    const gml = [31, isLeapG ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

    let g_day_no = 365 * g_y
      + _f((g_y + 3) / 4)
      - _f((g_y + 99) / 100)
      + _f((g_y + 399) / 400);
    for (let i = 0; i < g_m; i++) g_day_no += gml[i];
    g_day_no += g_d;

    let j_day_no = g_day_no - 79;
    const j_np   = _f(j_day_no / 12053);
    j_day_no     = j_day_no % 12053;

    let jy   = 979 + 33 * j_np + 4 * _f(j_day_no / 1461);
    j_day_no = j_day_no % 1461;

    if (j_day_no >= 366) {
      jy       += _f((j_day_no - 1) / 365);
      j_day_no  = (j_day_no - 1) % 365;
    }

    const jml = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29];
    let jm = 0, jd = 0;
    for (let i = 0; i < 12; i++) {
      if (j_day_no < jml[i]) { jm = i + 1; jd = j_day_no + 1; break; }
      j_day_no -= jml[i];
    }
    return { jy, jm, jd };
  }

  function toGregorian(jy, jm, jd) {
    const jy2 = jy - 979, jm2 = jm - 1, jd2 = jd - 1;
    let j_day_no = 365 * jy2 + _f(jy2 / 33) * 8 + _f((jy2 % 33 + 3) / 4);
    const jml    = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29];
    for (let i = 0; i < jm2; i++) j_day_no += jml[i];
    j_day_no += jd2;

    let g_day_no = j_day_no + 79;
    let gy       = 1600 + 400 * _f(g_day_no / 146097);
    g_day_no     = g_day_no % 146097;

    let leap = true;
    if (g_day_no >= 36525) {
      g_day_no--;
      gy      += 100 * _f(g_day_no / 36524);
      g_day_no = g_day_no % 36524;
      if (g_day_no >= 365) g_day_no++; else leap = false;
    }
    gy      += 4 * _f(g_day_no / 1461);
    g_day_no = g_day_no % 1461;
    if (g_day_no >= 366) {
      leap     = false;
      g_day_no--;
      gy      += _f(g_day_no / 365);
      g_day_no = g_day_no % 365;
    }

    const gml = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let gm = 0, gd = 0;
    for (let i = 0; i < 12; i++) {
      if (g_day_no < gml[i]) { gm = i + 1; gd = g_day_no + 1; break; }
      g_day_no -= gml[i];
    }
    return { gy, gm, gd };
  }

  function daysInMonth(jy, jm) {
    if (jm <= 6)  return 31;
    if (jm <= 11) return 30;
    return isLeap(jy) ? 30 : 29;
  }

  function isLeap(jy) {
    return [1, 5, 9, 13, 17, 22, 26, 30].includes(jy % 33);
  }

  function weekdayOfFirst(jy, jm) {
    const g = toGregorian(jy, jm, 1);
    return (new Date(g.gy, g.gm - 1, g.gd).getDay() + 1) % 7;
  }

  function today() {
    const n = new Date();
    return toJalali(n.getFullYear(), n.getMonth() + 1, n.getDate());
  }

  const pad = n => (n < 10 ? '0' : '') + n;
  const pd  = s => String(s).replace(/[0-9]/g, d => '۰۱۲۳۴۵۶۷۸۹'[+d]);

  function toISO(jy, jm, jd) {
    const g = toGregorian(jy, jm, jd);
    return `${g.gy}-${pad(g.gm)}-${pad(g.gd)}`;
  }

  function toStr(jy, jm, jd) {
    return `${pd(jy)}/${pd(pad(jm))}/${pd(pad(jd))}`;
  }

  return { toJalali, toGregorian, daysInMonth, isLeap, weekdayOfFirst, today, toISO, toStr, pd, pad };
})();


// ══════════════════════════════════════════════════════════
// DatePicker class
// ══════════════════════════════════════════════════════════
class PersianDatePicker {
  constructor({ trigger, hiddenInput, placeholder = 'انتخاب تاریخ', onChange = null }) {
    this.trigger  = trigger;
    this.hidden   = hiddenInput;
    this.onChange = onChange;

    const t = Jalali.today();
    // مقدار انتخاب‌شده خالی — کاربر باید خودش انتخاب کند
    this.selY  = null; this.selM  = null; this.selD  = null;
    // نمای تقویم روی ماه/سال جاری باز می‌شود
    this.viewY = t.jy;  this.viewM = t.jm;

    this.mode     = 'days';
    this.yearPage = 0;
    this.open     = false;
    this.el       = null;

    this._build();
    this._commit();
  }

  _build() {
    this.trigger.readOnly     = true;
    this.trigger.style.cursor = 'pointer';
    this.trigger.addEventListener('click', e => { e.stopPropagation(); this._toggle(); });
    document.addEventListener('click', e => {
      if (this.open && this.el && !this.el.contains(e.target)) this._close();
    });
    this.el = document.createElement('div');
    this.el.className = 'pdp-container';
    this.el.addEventListener('click', e => e.stopPropagation());
    document.body.appendChild(this.el);
  }

  _commit() {
    if (this.selY && this.selM && this.selD) {
      this.trigger.value = Jalali.toStr(this.selY, this.selM, this.selD);
      this.hidden.value  = Jalali.toISO(this.selY, this.selM, this.selD);
    } else {
      this.trigger.value = '';
      this.hidden.value  = '';
    }
    if (this.onChange) this.onChange(this.hidden.value, this.trigger.value);
  }

  _toggle() { this.open ? this._close() : this._open(); }

  _open() {
    this.open = true;
    this.mode = 'days';
    this._pos();
    this._render();
    this.el.classList.add('pdp-visible');
  }

  _close() {
    this.open = false;
    this.el.classList.remove('pdp-visible');
  }

  _pos() {
    const r  = this.trigger.getBoundingClientRect();
    const sY = window.scrollY || 0;
    const sX = window.scrollX || 0;
    let top  = r.bottom + sY + 8;
    let left = r.right  + sX - 300;
    if (left < 8) left = 8;
    if (left + 300 > window.innerWidth - 8) left = window.innerWidth - 308;
    if (r.bottom + 390 > window.innerHeight) top = r.top + sY - 390;
    if (top < sY + 8) top = sY + 8;
    Object.assign(this.el.style, { top: top + 'px', left: left + 'px' });
  }

  _render() {
    this.mode === 'years' ? this._renderYears() : this._renderDays();
  }

  _renderDays() {
    const MONTHS   = ['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
    const WEEKDAYS = ['ش','ی','د','س','چ','پ','ج'];
    const tod   = Jalali.today();
    const first = Jalali.weekdayOfFirst(this.viewY, this.viewM);
    const total = Jalali.daysInMonth(this.viewY, this.viewM);

    let cells = '<div class="pdp-cell pdp-empty"></div>'.repeat(first);
    for (let d = 1; d <= total; d++) {
      const isT = d === tod.jd && this.viewM === tod.jm && this.viewY === tod.jy;
      const isS = d === this.selD && this.viewM === this.selM && this.viewY === this.selY;
      cells += `<div class="pdp-cell pdp-day${isT ? ' pdp-today' : ''}${isS ? ' pdp-selected' : ''}" data-d="${d}">${Jalali.pd(d)}</div>`;
    }

    this.el.innerHTML = `
      <div class="pdp-header">
        <button class="pdp-nav pdp-prev" title="ماه قبل">&#8249;</button>
        <div class="pdp-title">
          <span class="pdp-month-lbl">${MONTHS[this.viewM - 1]}</span>
          <button class="pdp-year-btn" title="انتخاب سال">${Jalali.pd(this.viewY)} ▾</button>
        </div>
        <button class="pdp-nav pdp-next" title="ماه بعد">&#8250;</button>
      </div>
      <div class="pdp-weekdays">${WEEKDAYS.map(w => `<div class="pdp-wday">${w}</div>`).join('')}</div>
      <div class="pdp-grid">${cells}</div>
      <div class="pdp-footer">
        <button class="pdp-btn-clear">پاک کردن</button>
        <button class="pdp-btn-today">امروز</button>
      </div>`;

    this.el.querySelector('.pdp-prev').onclick      = () => this._prevMonth();
    this.el.querySelector('.pdp-next').onclick      = () => this._nextMonth();
    this.el.querySelector('.pdp-btn-today').onclick = () => this._pickToday();
    this.el.querySelector('.pdp-btn-clear').onclick = () => this._clear();
    this.el.querySelector('.pdp-year-btn').onclick  = () => { this.mode = 'years'; this._render(); };
    this.el.querySelectorAll('.pdp-day').forEach(el =>
      el.addEventListener('click', () => this._pick(+el.dataset.d))
    );
  }

  _renderYears() {
    const tod  = Jalali.today();
    const base = (this.viewY - (this.viewY % 12)) + this.yearPage * 12;
    const cells = Array.from({ length: 12 }, (_, i) => {
      const y    = base + i;
      const isCur = y === tod.jy;
      const isSel = y === this.viewY;
      return `<div class="pdp-yr-cell${isCur ? ' pdp-yr-now' : ''}${isSel ? ' pdp-yr-sel' : ''}" data-y="${y}">${Jalali.pd(y)}</div>`;
    }).join('');

    this.el.innerHTML = `
      <div class="pdp-header pdp-header-yr">
        <button class="pdp-nav pdp-yr-prev" title="قبل">&#8249;</button>
        <div class="pdp-title">
          <span class="pdp-yr-range">${Jalali.pd(base)} – ${Jalali.pd(base + 11)}</span>
        </div>
        <button class="pdp-nav pdp-yr-next" title="بعد">&#8250;</button>
      </div>
      <div class="pdp-yr-grid">${cells}</div>
      <div class="pdp-footer">
        <button class="pdp-btn-back">↩ برگشت</button>
        <button class="pdp-btn-today">امروز</button>
      </div>`;

    this.el.querySelector('.pdp-yr-prev').onclick   = () => { this.yearPage--; this._render(); };
    this.el.querySelector('.pdp-yr-next').onclick   = () => { this.yearPage++; this._render(); };
    this.el.querySelector('.pdp-btn-back').onclick  = () => { this.mode = 'days'; this._render(); };
    this.el.querySelector('.pdp-btn-today').onclick = () => this._pickToday();
    this.el.querySelectorAll('.pdp-yr-cell').forEach(el =>
      el.addEventListener('click', () => {
        this.viewY    = +el.dataset.y;
        this.yearPage = 0;
        this.mode     = 'days';
        this._render();
      })
    );
  }

  _prevMonth() {
    if (this.viewM === 1) { this.viewM = 12; this.viewY--; } else this.viewM--;
    this._render();
  }
  _nextMonth() {
    if (this.viewM === 12) { this.viewM = 1; this.viewY++; } else this.viewM++;
    this._render();
  }

  _pick(d) {
    this.selY = this.viewY; this.selM = this.viewM; this.selD = d;
    this._commit();
    this._render();
    setTimeout(() => this._close(), 160);
  }

  _pickToday() {
    const t = Jalali.today();
    this.viewY = t.jy; this.viewM = t.jm;
    this.selY  = t.jy; this.selM  = t.jm; this.selD = t.jd;
    this._commit();
    this._render();
    setTimeout(() => this._close(), 160);
  }

  _clear() {
    this.selY = null; this.selM = null; this.selD = null;
    this._commit();
    this._render();
  }
}

// ══════════════════════════════════════════════════════════
// Auto-init
// ══════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-pdp]').forEach(wrapper => {
    const trigger = wrapper.querySelector('.pdp-trigger');
    const hidden  = wrapper.querySelector('.pdp-value');
    if (!trigger || !hidden) return;
    new PersianDatePicker({
      trigger,
      hiddenInput: hidden,
      placeholder: wrapper.dataset.placeholder || 'انتخاب تاریخ',
    });
  });
});
