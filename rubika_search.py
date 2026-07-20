"""
Rubika Web automation با Persistent Chrome Profile (user_data_dir).

هر اکانت یه پوشه Chrome جداگانه داره — IndexedDB/cookies/localStorage
همه روی دیسک ماندگارند و نیاز به login مجدد نیست.

وب روبیکا (web.rubika.ir) یک اپلیکیشن Angular (فورکِ tweb) است، پس (سلکتورهای واقعی):
  • جستجو از باکسِ کناری (.input-search-input) انجام می‌شود؛ نتایج در
    .search-super / .search-super-item / .chatlist-chat ظاهر می‌شوند.
  • برخلاف ایتا/بله، روبیکا «جستجوی کانال‌به‌کانال» و «جستجوی سراسری»
    جداگانه ندارد — فقط همین یک جستجوی عادی. (طبق خواستهٔ کاربر با الگوی بله.)
  • ری‌اکشن‌ها: .reactions.reactions-block > .reaction.reaction-block
    (شمارش .reaction-counter، استیکر .reaction-sticker)؛ بازدید با آیکن
    .rbico-channelviews؛ متن پیام [rb-message-text]/.message.

Public API:
  search_all_accounts(accounts, ...)  → جستجوی موازی
  profile_has_data(user_data_dir)     → آیا پروفایل Chrome آماده است؟
"""

import asyncio
import os
import re
from datetime import date, timedelta
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from khayyam import JalaliDate

from config import RUBIKA_URL, SEARCH_TIMEOUT_MS, MAX_CONCURRENT

# ── قفل per-account ──────────────────────────────────────────────────────────
# اگر دو job هم‌زمان بخواهند از همان اکانت (همان پروفایل Chrome) استفاده کنند،
# launch_persistent_context روی پروفایل دوم خطای SingletonLock می‌دهد.
_account_locks: dict[str, asyncio.Lock] = {}


def _get_account_lock(account_id: str) -> asyncio.Lock:
    lock = _account_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _account_locks[account_id] = lock
    return lock


# ── آرگومان‌های مشترک Chromium ────────────────────────────────────────────────
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
]

# باکس جستجوی کناری
SEARCH_SELECTORS = [
    ".sidebar-search .input-search-input",
    "#column-left .input-search-input",
    ".input-search .input-search-input",
    "input.input-search-input",
    ".input-search-input",
    "input[type='search']",
    "input[placeholder*='جستجو']",
]

# ══════════════════════════════════════════════════════════════════════════════
# helper های متن/عدد
# ══════════════════════════════════════════════════════════════════════════════
_SHORTCODE_RE = re.compile(r':[A-Za-z0-9_+\-]+:')          # اموجی‌های شورت‌کد مثل :red_circle:
_KEEP_LETTERS_RE = re.compile(r'[^A-Za-z\u0600-\u06FF]')   # فقط حروفِ لاتین و عربی/فارسی
_AR_DIGIT_PUNCT_RE = re.compile(r'[\u0660-\u0669\u06F0-\u06F9\u060C\u061B\u061F\u066A-\u066D\u200c]')


def _normalize(s: str) -> str:
    """
    برای تطبیقِ تقریبیِ اسنیپت با محتوای حباب: اموجی‌ها/شورت‌کدها، فاصله‌ها،
    اعداد و نشانه‌ها حذف می‌شوند تا فقط حروفِ متن بماند.
    """
    s = _SHORTCODE_RE.sub('', s or '')
    s = _KEEP_LETTERS_RE.sub('', s)       # حذف اموجی/نشانه/فاصله (هرچه حرف نیست)
    s = _AR_DIGIT_PUNCT_RE.sub('', s)     # حذف ارقام و نشانه‌های عربی که در بازهٔ حروف بودند
    return s


def _snippet_matches(snippet: str, full_content: str) -> bool:
    """
    تأیید می‌کند که محتوای کاملِ گرفته‌شده از حباب، همان پیامِ نتیجهٔ
    کلیک‌شده است (اسنیپتِ نتیجه معمولاً ابتدای پیام است).
    """
    a = _normalize(snippet)
    b = _normalize(full_content)
    if not a or not b:
        return False
    key = a[:16]
    return key in b or b[:16] in a


def _to_latin_digits(s: str) -> str:
    """تبدیل ارقام فارسی/عربی به لاتین."""
    table = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
    return s.translate(table)


def _parse_count(s: str) -> int:
    """
    شمارش‌ها را به عدد تبدیل می‌کند — با پشتیبانی از پسوندهای «هزار»/K/M و
    ارقام فارسی. مثال: «۸K»→8000، «2.5M»→2500000، «۱۲۳»→123.
    """
    if not s:
        return 0
    t = _to_latin_digits(str(s)).strip().replace(',', '').replace('،', '')
    if not t:
        return 0
    mult = 1
    low = t.lower()
    if low.endswith('k') or 'هزار' in t:
        mult = 1_000
        low = low.replace('k', '')
    elif low.endswith('m') or 'میلیون' in t:
        mult = 1_000_000
        low = low.replace('m', '')
    low = re.sub(r'[^0-9.]', '', low)
    if not low:
        return 0
    try:
        return int(float(low) * mult)
    except ValueError:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# JS: جمع‌آوری نتایجِ گروهِ «پیام‌ها» از باکس جستجوی کناری
# ══════════════════════════════════════════════════════════════════════════════
_JS_COLLECT_RESULTS = r"""
() => {
  // فقط ردیف‌های گروهِ «پیام‌ها» (نه «جستجوی گسترده»/گفتگوها/مخاطبین).
  // ساختار واقعیِ روبیکا (Angular):
  //   .search-group-messages > ul.chatlist > li  با
  //   .peer-title (عنوان چت)، .dialog-subtitle/.user-last-message (اسنیپت)،
  //   .message-time (زمان). این ردیف‌ها data-mid ندارند؛ با ایندکس کلیک می‌کنیم.
  const grp = document.querySelector('.search-group-messages');
  if (!grp) return [];
  const rows = [...grp.querySelectorAll('ul.chatlist > li')];
  const out = [];
  rows.forEach((r, i) => {
    // ترتیب مهم است: .peer-title اول، وگرنه querySelectorِ چندگانه ممکن است
    // .dialog-title (والدِ عنوان+زمان) را برگرداند و زمان به عنوان بچسبد.
    const titleEl = r.querySelector('.peer-title') || r.querySelector('.user-title') || r.querySelector('.dialog-title');
    const subEl   = r.querySelector('.dialog-subtitle, .user-last-message');
    const timeEl  = r.querySelector('.message-time, .dialog-time');
    const title = titleEl ? (titleEl.textContent || '').trim() : '';
    const sub   = subEl   ? (subEl.textContent   || '').trim() : '';
    const time  = timeEl  ? (timeEl.textContent  || '').trim() : '';
    if (!sub && !title) return;
    r.setAttribute('data-basir-idx', String(i));
    out.push({idx: i, title, subtitle: sub, time, mid: '', peerId: ''});
  });
  return out;
}
"""

_JS_SCROLL_RESULTS = r"""
() => {
  const grp = document.querySelector('.search-group-messages');
  const sc = (grp && (grp.closest('.scrollable') ||
                      grp.closest('.search-super-container-chats') ||
                      grp.closest('.sidebar-content'))) ||
             document.querySelector('.search-super .scrollable') ||
             document.querySelector('.sidebar-search .scrollable');
  if (!sc) return -1;
  const before = sc.scrollTop;
  sc.scrollTop = sc.scrollHeight;
  return Math.round(sc.scrollTop) - Math.round(before);
}
"""

# استخراجِ کاملِ محتوا/ری‌اکشن/بازدید از حبابِ پیام در چتِ باز شده.
# چون ردیف‌های نتیجه data-mid ندارند، حبابِ درست را با تطبیقِ اسنیپت پیدا می‌کنیم.
_JS_EXTRACT_BUBBLE = r"""
(snippet) => {
  const norm = s => (s || '')
    .replace(/:[A-Za-z0-9_+\-]+:/g, '')          // شورت‌کدهای اموجی
    .replace(/[^A-Za-z\u0600-\u06FF]/g, '')      // فقط حروفِ لاتین و عربی/فارسی
    .replace(/[\u0660-\u0669\u06F0-\u06F9\u060C\u061B\u061F]/g, '');  // ارقام/نشانه‌های عربی
  const key = norm(snippet).slice(0, 16);

  document.querySelectorAll('[data-basir-target]').forEach(e => e.removeAttribute('data-basir-target'));

  const bubbles = [...document.querySelectorAll('.bubbles .bubble, .bubble')];
  let bubble = null;
  if (key) {
    for (const b of bubbles) {
      const m = b.querySelector('.message');
      if (!m) continue;
      const t = norm(m.textContent);
      if (t && (t.includes(key) || key.includes(t.slice(0, 16)))) { bubble = b; break; }
    }
  }
  if (!bubble) {
    bubble = document.querySelector(
      '.bubbles .bubble.is-highlighted, .bubbles .bubble.backlight, .bubbles .bubble.is-selected');
  }
  if (!bubble) return null;
  bubble.setAttribute('data-basir-target', '1');

  // محتوای متنِ پیام (بدون زمان/بازدید/ری‌اکشن)
  let content = '';
  const textEl = bubble.querySelector('[rb-message-text], .message, .text-content, .translatable-message');
  if (textEl) {
    const clone = textEl.cloneNode(true);
    clone.querySelectorAll('.time, [rb-message-time], .reactions, .reactions-block').forEach(e => e.remove());
    content = (clone.innerText || clone.textContent || '').trim();
  }

  // ری‌اکشن‌ها: .reactions .reaction.reaction-block > .reaction-sticker .emoji[title] + .reaction-counter
  const reactions = [...bubble.querySelectorAll('.reactions .reaction.reaction-block, .reactions-block .reaction.reaction-block')].map(rc => {
    const cntEl = rc.querySelector('.reaction-counter');
    const count = cntEl ? (cntEl.textContent || '').trim() : '';
    let emoji = '';
    const em = rc.querySelector('.reaction-sticker .emoji, .emoji, .reaction-sticker span[title]');
    if (em) emoji = (em.getAttribute('title') || em.textContent || '').trim();
    if (!emoji) {
      const img = rc.querySelector('img');
      if (img) emoji = (img.getAttribute('alt') || '').trim();
    }
    if (!emoji) {
      emoji = (rc.textContent || '').replace(/[0-9\u06F0-\u06F9,\u060C.KMkm\s]/g, '').trim();
    }
    return {emoji, count};
  }).filter(r => r.count);

  // زمان و بازدید هر دو داخلِ [rb-message-time].time هستند؛ از نسخهٔ .inner می‌خوانیم.
  let views = '', time = '';
  const timeRoot = bubble.querySelector('[rb-message-time], .time');
  if (timeRoot) {
    const inner = timeRoot.querySelector('.inner') || timeRoot;
    const spans = [...inner.querySelectorAll('span')].reverse();
    const ts = spans.find(s => /\d{1,2}:\d{2}/.test(s.textContent));
    time = ts ? ts.textContent.trim() : '';
    const hasViews = !!(timeRoot.querySelector('.rbico-channelviews') || bubble.querySelector('.rbico-channelviews'));
    if (hasViews) {
      let txt = (inner.textContent || '').replace(/\s+/g, ' ').trim();
      if (time) txt = txt.replace(time, ' ');
      const vm = txt.match(/[\d\u06F0-\u06F9][\d\u06F0-\u06F9.,]*\s?[KMkm]?/);
      views = vm ? vm[0].replace(/\s+/g, '').trim() : '';
    }
  }

  return {content, reactions, views, time};
}
"""

# متن گزینهٔ «رونوشت لینک پیام» در منوی کلیک‌راست را کلیک می‌کند (کانال‌ها).
_JS_COPY_LINK = r"""
() => {
  const wanted = ['رونوشت لینک', 'کپی لینک', 'رونوشت پیوند', 'کپی پیوند', 'Copy Link', 'Copy Message Link'];
  const items = [...document.querySelectorAll('.btn-menu-item, .menu-item, [role=menuitem]')];
  for (const it of items) {
    const t = (it.textContent || '').trim();
    if (wanted.some(w => t.includes(w))) { it.click(); return true; }
  }
  return false;
}
"""


async def _first_selector(page, selectors, timeout):
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


async def _extract_message_details(page, mid: str, snippet: str) -> dict:
    """
    بعد از باز شدن چت و پرش به پیام، محتوای کامل، ری‌اکشن‌ها، بازدید و لینک را
    مستقیماً از حبابِ پیام استخراج می‌کند. اگر محتوا با اسنیپتِ نتیجه هم‌خوان
    نبود، دیکشنری خالی برمی‌گرداند تا پیامِ اشتباه نمایش داده نشود.
    """
    details = {
        'full_content': '',
        'reactions': [],
        'total_reactions': 0,
        'views': '',
        'datetime_full': '',
        'message_link': '',
    }

    data = None
    for _ in range(24):
        await asyncio.sleep(0.5)
        try:
            data = await page.evaluate(_JS_EXTRACT_BUBBLE, snippet)
        except Exception:
            data = None
        if data and (data.get('content') or data.get('reactions')):
            break

    if not data:
        print(f"[Basir] حباب پیام پیدا نشد: {snippet[:40]}")
        return details

    full_content = (data.get('content') or '').strip()
    # تأیید تطابق محتوا با نتیجهٔ کلیک‌شده
    if snippet and full_content and not _snippet_matches(snippet, full_content):
        print(f"[Basir] محتوای نامنطبق با نتیجه — دور ریخته شد: {snippet[:40]}")
        return details

    details['full_content'] = full_content or snippet
    reactions = data.get('reactions', []) or []
    details['reactions'] = reactions
    details['total_reactions'] = sum(_parse_count(r.get('count', '0')) for r in reactions)
    details['views'] = data.get('views', '') or ''
    details['datetime_full'] = data.get('time', '') or ''

    # تلاشِ اختیاری برای گرفتنِ لینکِ پیام (کانال‌ها) از منوی کلیک‌راست
    try:
        bubble = await page.query_selector('.bubble[data-basir-target="1"] .bubble-content')
        if bubble is None:
            bubble = await page.query_selector('.bubble[data-basir-target="1"]')
        if bubble is not None and await bubble.is_visible():
            await bubble.click(button="right", timeout=4000)
            await asyncio.sleep(0.6)
            clicked = await page.evaluate(_JS_COPY_LINK)
            if clicked:
                await asyncio.sleep(0.6)
                try:
                    link = await page.evaluate("() => navigator.clipboard.readText()")
                except Exception:
                    link = ""
                if link and 'rubika' in link:
                    details['message_link'] = link
            else:
                await page.keyboard.press("Escape")
    except Exception:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    return details


# ══════════════════════════════════════════════════════════════════════════════
# تبدیل تاریخ جلالی ↔ گرگوری (Python)
# ══════════════════════════════════════════════════════════════════════════════

def _jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """جلالی → گرگوری  |  تست: (1405,3,20) → (2026,6,10) ✓"""
    jy2 = jy - 979
    jm2 = jm - 1
    jd2 = jd - 1
    j_day_no = (365 * jy2 + (jy2 // 33) * 8 + (jy2 % 33 + 3) // 4)
    jml = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    for i in range(jm2):
        j_day_no += jml[i]
    j_day_no += jd2
    g_day_no = j_day_no + 79
    gy = 1600 + 400 * (g_day_no // 146097)
    g_day_no = g_day_no % 146097
    leap = True
    if g_day_no >= 36525:
        g_day_no -= 1
        gy += 100 * (g_day_no // 36524)
        g_day_no = g_day_no % 36524
        if g_day_no >= 365:
            g_day_no += 1
        else:
            leap = False
    gy += 4 * (g_day_no // 1461)
    g_day_no = g_day_no % 1461
    if g_day_no >= 366:
        leap = False
        g_day_no -= 1
        gy += g_day_no // 365
        g_day_no = g_day_no % 365
    gml = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = gd = 0
    for i in range(12):
        if g_day_no < gml[i]:
            gm = i + 1
            gd = g_day_no + 1
            break
        g_day_no -= gml[i]
    return gy, gm, gd


def _gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """گرگوری → جلالی  |  تست: (2026,6,10) → (1405,3,20) ✓"""
    g_y = gy - 1600
    g_m = gm - 1
    g_d = gd - 1
    is_leap_g = (gy % 4 == 0) and (gy % 100 != 0 or gy % 400 == 0)
    gml = [31, 29 if is_leap_g else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    g_day_no = (365 * g_y
                + (g_y + 3) // 4
                - (g_y + 99) // 100
                + (g_y + 399) // 400)
    for i in range(g_m):
        g_day_no += gml[i]
    g_day_no += g_d
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no = j_day_no % 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no = j_day_no % 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    jml = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    jm = jd = 0
    for i in range(12):
        if j_day_no < jml[i]:
            jm = i + 1
            jd = j_day_no + 1
            break
        j_day_no -= jml[i]
    return jy, jm, jd


# نگاشت نام‌های فارسی
_PERSIAN_MONTHS = {
    'فروردین': 1, 'اردیبهشت': 2, 'خرداد': 3,
    'تیر': 4,    'مرداد': 5,    'شهریور': 6,
    'مهر': 7,    'آبان': 8,     'آذر': 9,
    'دی': 10,    'بهمن': 11,    'اسفند': 12,
}

# نگاشت روز هفته فارسی → Python weekday (Mon=0 … Sun=6)
_PERSIAN_WEEKDAYS = {
    'شنبه': 5, 'یکشنبه': 6, 'دوشنبه': 0,
    'سه‌شنبه': 1, 'سه شنبه': 1,
    'چهارشنبه': 2, 'پنجشنبه': 3, 'جمعه': 4,
}


def parse_rubika_date(date_str: str) -> date | None:
    """
    رشته تاریخ روبیکا را به date میلادی تبدیل می‌کند.

    فرمت‌های پشتیبانی‌شده:
      "13:40"           → امروز
      "دیروز"           → روز قبل
      "دوشنبه"          → آن روز هفته در ۷ روز اخیر
      "23 خرداد"        → روز + ماه شمسی در سال جاری (یا قبلی)
      "20 خرداد 1405"   → روز + ماه + سال شمسی کامل
      "1402/10/28"      → تاریخ کامل شمسی با اسلش
    """
    if not date_str or date_str in ('—', '-', ''):
        return None

    s = _to_latin_digits(date_str.strip())
    s = s.strip('()').strip()
    today = date.today()

    m = re.match(r'^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$', s)
    if m:
        try:
            jy, jm, jd = int(m.group(1)), int(m.group(2)), int(m.group(3))
            gy, gm, gd = _jalali_to_gregorian(jy, jm, jd)
            return date(gy, gm, gd)
        except Exception:
            return None

    m_full = re.match(r'^(\d{1,2})\s+(\S+)\s+(\d{4})$', s)
    if m_full:
        jd = int(m_full.group(1))
        month_name = m_full.group(2).strip()
        jy = int(m_full.group(3))
        jm = _PERSIAN_MONTHS.get(month_name)
        if jm:
            try:
                gy, gm, gd = _jalali_to_gregorian(jy, jm, jd)
                return date(gy, gm, gd)
            except Exception:
                return None
        return None

    if re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', s):
        return today

    original = date_str.strip()
    if original in ('دیروز', 'ديروز'):
        return today - timedelta(days=1)

    for day_name, py_weekday in _PERSIAN_WEEKDAYS.items():
        if original == day_name:
            days_ago = (today.weekday() - py_weekday) % 7
            if days_ago == 0:
                days_ago = 7
            return today - timedelta(days=days_ago)

    m2 = re.match(r'^(\d{1,2})\s+(.+)$', s)
    if m2:
        jd = int(m2.group(1))
        month_name = m2.group(2).strip()
        jm = _PERSIAN_MONTHS.get(month_name)
        if jm:
            cur_jy, cur_jm, cur_jd = _gregorian_to_jalali(today.year, today.month, today.day)
            jy = cur_jy
            if (jm, jd) > (cur_jm, cur_jd):
                jy -= 1
            try:
                gy, gm, gd = _jalali_to_gregorian(jy, jm, jd)
                return date(gy, gm, gd)
            except Exception:
                return None

    return None


def profile_has_data(user_data_dir: str) -> bool:
    """چک می‌کنه آیا پروفایل Chrome وجود داره و داده داره."""
    default_dir = os.path.join(user_data_dir, "Default")
    if not os.path.isdir(default_dir):
        return False
    try:
        items = os.listdir(default_dir)
        return len(items) >= 2
    except OSError:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# جستجو با یه اکانت
# ══════════════════════════════════════════════════════════════════════════════
async def _search_one(account: dict, query: str,
                      date_from: str, date_to: str,
                      semaphore: asyncio.Semaphore) -> list[dict]:
    results = []
    user_data_dir = account.get("user_data_dir", "")

    if not profile_has_data(user_data_dir):
        print(f"[Basir] «{account['name']}» پروفایل ندارد — skip")
        return []

    async with semaphore:
        account_lock = _get_account_lock(account["id"])
        async with account_lock:
            async with async_playwright() as p:
                ctx = None
                try:
                    ctx = await p.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        headless=False,
                        args=_CHROME_ARGS,
                        user_agent=_USER_AGENT,
                        viewport={"width": 1280, "height": 800},
                        locale="fa-IR",
                        timezone_id="Asia/Tehran",
                        permissions=["clipboard-read", "clipboard-write"],
                    )

                    pages = ctx.pages
                    page = pages[0] if pages else await ctx.new_page()

                    await page.goto(RUBIKA_URL, wait_until="domcontentloaded", timeout=30_000)
                    await asyncio.sleep(3)

                    # بررسی لاگین بودن
                    logged_in = await _first_selector(page, LOGGED_IN_SELECTORS, timeout=6_000)
                    if not logged_in:
                        print(f"[Basir] «{account['name']}» session منقضی — skip")
                        return []

                    print(f"[Basir] «{account['name']}» — جستجو: {query}")

                    # پیدا کردن باکس جستجو
                    search_el = await _first_selector(page, SEARCH_SELECTORS, timeout=12_000)
                    if not search_el:
                        print(f"[Basir] باکس جستجو برای «{account['name']}» پیدا نشد")
                        return []

                    await search_el.click()
                    await search_el.fill(query)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                    print("Started Searching ... ")

                    # ── جمع‌آوری نتایجِ پیام‌ها ─────────────────────────────
                    # هر تکرار: باکس جستجو را فعال نگه می‌داریم، لیستِ نتایج را
                    # تازه جمع می‌کنیم (چون کلیک روی یک نتیجه و باز شدن چت،
                    # عناصرِ <li> را در Angular بازسازی و data-basir-idx را پاک
                    # می‌کند)، سپس اولین ردیفِ دیده‌نشده را کلیک و استخراج می‌کنیم.
                    MessageTemp = []          # پیام‌های نهاییِ استخراج‌شده
                    seen = set()              # کلیدِ یکتا برای جلوگیری از تکرار
                    no_progress = 0

                    for _round in range(300):
                        # ۱) اطمینان از فعال بودنِ جستجو (کوئری حفظ شده باشد)
                        try:
                            sb = await _first_selector(page, SEARCH_SELECTORS, timeout=4_000)
                            if sb:
                                try:
                                    cur = (await sb.input_value()) or ""
                                except Exception:
                                    cur = ""
                                if cur.strip() != query:
                                    await sb.click()
                                    await sb.fill(query)
                                    await page.keyboard.press("Enter")
                                    await asyncio.sleep(2)
                                else:
                                    await sb.click()
                                    await asyncio.sleep(0.3)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass

                        # ۲) جمع‌آوریِ تازهٔ ردیف‌های گروهِ «پیام‌ها»
                        try:
                            rows = await page.evaluate(_JS_COLLECT_RESULTS)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            rows = []
                            print(f"collect error: {e}")

                        # ۳) اولین ردیفِ دیده‌نشده
                        target = None
                        for row in rows:
                            title   = (row.get('title') or '').strip()
                            snippet = (row.get('subtitle') or '').strip()
                            rdate   = (row.get('time') or '').strip()
                            key = (title, snippet, rdate)
                            if not snippet or key in seen:
                                continue
                            target = (row, title, snippet, rdate, key)
                            break

                        if target is None:
                            # چیزی برای پردازش نمانده → اسکرول برای بارگذاریِ بیشتر
                            try:
                                moved = await page.evaluate(_JS_SCROLL_RESULTS)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                moved = 0
                            await asyncio.sleep(1.2)
                            no_progress += 1
                            if no_progress >= 4:
                                break
                            continue

                        row, title, snippet, rdate, key = target
                        seen.add(key)
                        no_progress = 0

                        # کلیک روی نتیجه → پرش به پیام در چت
                        try:
                            await page.click(f'[data-basir-idx="{row["idx"]}"]', timeout=8_000)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            print(f"row click failed: {e}")
                            continue
                        await asyncio.sleep(2.2)

                        msg_details = await _extract_message_details(page, "", snippet)
                        newMessage = {
                            'Title':   title,
                            'Date':    rdate,
                            'Content': snippet,
                            'full_content':    msg_details.get('full_content', ''),
                            'reactions':       msg_details.get('reactions', []),
                            'total_reactions': msg_details.get('total_reactions', 0),
                            'views':           msg_details.get('views', ''),
                            'datetime_full':   msg_details.get('datetime_full', ''),
                            'message_link':    msg_details.get('message_link', ''),
                        }
                        MessageTemp.append(newMessage)
                        print(f"[Basir] Details: views={newMessage['views']}, "
                              f"reactions={newMessage['total_reactions']}")

                    # ── فیلتر تاریخ + ساخت خروجی ─────────────────────────────
                    for Message in MessageTemp:
                        try:
                            if date_from or date_to:
                                msg_date = parse_rubika_date(Message.get('Date', ''))
                                if msg_date is not None:
                                    if date_from:
                                        try:
                                            df = date.fromisoformat(date_from)
                                            if msg_date < df:
                                                continue
                                        except ValueError:
                                            pass
                                    if date_to:
                                        try:
                                            dt = date.fromisoformat(date_to)
                                            if msg_date > dt:
                                                continue
                                        except ValueError:
                                            pass

                            # فقط پیام‌هایی که محتوای کاملشان استخراج شده در خروجی می‌آیند
                            msg_full = (Message.get('full_content') or '').strip()
                            if not msg_full:
                                print(f"[Basir] پیامِ ناقص (بدون محتوا) — نمایش داده نمی‌شود: "
                                      f"{(Message.get('Content') or '')[:40]}")
                                continue

                            CorrectDate = parse_rubika_date(Message.get('Date'))
                            if CorrectDate is not None:
                                Shamsi = str(JalaliDate(CorrectDate))
                            else:
                                Shamsi = Message.get('Date') or "—"

                            results.append({
                                "channel_name": Message.get('Title'),
                                "message": Message.get('Content'),
                                "date": Shamsi or "—",
                                "account_name": account["name"],
                                "account_id": account["id"],
                                "full_content": Message.get('full_content', ''),
                                "reactions": Message.get('reactions', []),
                                "total_reactions": Message.get('total_reactions', 0),
                                "views": Message.get('views', ''),
                                "datetime_full": Message.get('datetime_full', ''),
                                "message_link": Message.get('message_link', ''),
                            })
                        except Exception as e:
                            print(f"[Basir] parse error: {e}")
                            continue

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[Basir] خطا «{account['name']}»: {e}")
                finally:
                    if ctx:
                        try:
                            await ctx.close()
                        except Exception:
                            pass

    return results


# ══════════════════════════════════════════════════════════════════════════════
# جستجوی موازی همه اکانت‌ها
# ══════════════════════════════════════════════════════════════════════════════
async def search_all_accounts(accounts: list[dict], query: str,
                               date_from: str = "", date_to: str = "") -> list[dict]:
    if not accounts:
        return []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [_search_one(a, query, date_from, date_to, semaphore) for a in accounts]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    combined = []
    for r in raw:
        if isinstance(r, list):
            combined.extend(r)
    return combined
