"""
کاوشِ کانال‌های روبیکا.

دو قابلیت:
  fetch_channel_info(raw)        → اطلاعاتِ کارتِ کانال (عنوان، آیدی، بیو،
                                   تعداد مشترک، عکس پروفایل)
  analyze_channel(username, n)   → بررسیِ n پستِ آخرِ کانال و محاسبهٔ آمار
                                   (بازدید، ری‌اکشن، میانگین‌ها، نرخ تعامل)

تفاوتِ مهم با بله: روبیکا صفحهٔ عمومیِ کانال (با متاتگِ og) ندارد —
`rubika.ir/<username>` فقط یک صفحهٔ «در روبیکا باز کن» است و هیچ داده‌ای در
HTML ندارد. پس هر دو قابلیت از داخلِ وب‌اپِ احرازشده کار می‌کنند: کانال با
باکسِ جستجو پیدا و باز می‌شود، اطلاعاتِ کارت از هدرِ چت و پنلِ «اطلاعات کانال»
خوانده می‌شود، و آمارِ پست‌ها مستقیماً از حباب‌های `channel-post` (که در روبیکا
بازدید و ری‌اکشن را همان‌جا دارند) استخراج می‌شود.
"""
import asyncio
import re

from playwright.async_api import async_playwright

import accounts as acc_store
import job_runner
from config import (
    RUBIKA_URL,
    EXPLORE_MAX_POSTS,
    CHANNEL_SEARCH_TIMEOUT_MS,
    CHANNEL_OPEN_TIMEOUT_MS,
    POSTS_LOAD_TIMEOUT_MS,
    POSTS_POLL_MS,
    PROFILE_PANEL_TIMEOUT_MS,
)
from rubika_search import (
    _CHROME_ARGS,
    _USER_AGENT,
    _close_context_menu,
    _first_selector,
    _get_account_lock,
    _gregorian_to_jalali,
    _parse_count,
    _read_clipboard,
    emojify,
    parse_rubika_date,
    profile_has_data,
    LOGGED_IN_SELECTORS,
    SEARCH_SELECTORS,
    LINK_TIMEOUT_MS,
    MENU_TIMEOUT_MS,
)

_USERNAME_RE = re.compile(r'(?:rubika\.ir|web\.rubika\.ir)/(?:#)?@?([A-Za-z0-9_]+)')


def normalize_username(raw: str) -> str:
    """ورودیِ کاربر (لینک/آیدی) را به آیدیِ خالصِ کانال تبدیل می‌کند."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.split("?")[0].split("#")[0].rstrip("/")
    m = _USERNAME_RE.search(s)
    if m:
        s = m.group(1)
    s = s.split("/")[-1]
    return s.lstrip("@").strip()


# ── ردیف‌های کانال در نتایج جستجو ───────────────────────────────────────────────
# در روبیکا کانال‌ها در گروهِ `.search-group.search-group-contacts` می‌آیند (نه
# گروهِ `-people` که مخاطبین است و نه `-recent` که اخیراً دیده‌شده‌هاست). هر ردیف
# عنوانِ کانال و «N مشترک» دارد ولی آیدی ندارد؛ پس بعد از بازکردن، آیدی از پنلِ
# اطلاعات بررسی می‌شود.
_JS_MARK_CHANNEL_ROWS = r"""
() => {
  const groups = [...document.querySelectorAll(
    '#column-left .search-group.search-group-contacts')];
  const rows = [];
  for (const g of groups) {
    if (/people|recent/i.test(g.className)) continue;
    [...g.querySelectorAll('ul.chatlist > li')].forEach(li => {
      const idx = rows.length;
      li.setAttribute('data-basir-ch', String(idx));
      const t = e => e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : '';
      rows.push({
        idx,
        title: t(li.querySelector('.peer-title')),
        subtitle: t(li.querySelector('.user-last-message')),
      });
    });
    break;
  }
  return rows;
}
"""

# هدرِ چتِ باز‌شده: عنوان + «N مشترک». هدرِ «گفتگوی صوتی» یک نوارِ روییِ تماس است
# و نباید با هدرِ واقعیِ چت اشتباه شود، پس صریحاً `.topbar .chat-info` می‌خوانیم.
_JS_CHAT_HEADER = r"""
() => {
  const t = e => e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : '';
  const info = document.querySelector(
    '#column-center .sidebar-header.topbar .chat-info, #column-center .chat-info');
  if (!info) return null;
  return {
    title: t(info.querySelector('.peer-title')),
    subtitle: t(info.querySelector('.info .subtitle, .bottom, .subtitle')),
    raw: t(info),
  };
}
"""

# پنلِ «اطلاعات کانال» (ستونِ راست): آیدیِ کانال، بیو و آواتار.
_JS_PROFILE_INFO = r"""
() => {
  const col = document.querySelector('#column-right');
  if (!col) return null;
  const t = e => e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : '';
  let username = '', bio = '';
  for (const row of col.querySelectorAll('.row, .sidebar-left-section .row')) {
    const title = t(row.querySelector('.row-subtitle, .row-title'));
    const full = t(row);
    if (/نام کاربری/.test(full)) {
      username = full.replace(/.*نام کاربری/, '').replace('@', '').trim();
    } else if (/^درباره/.test(full)) {
      bio = full.replace(/^درباره/, '').trim();
    } else if (!bio && title && /درباره/.test(title)) {
      bio = full.replace(/درباره/, '').trim();
    }
  }
  const img = col.querySelector('.profile-avatars img, .avatar-element img');
  const letterEl = col.querySelector('.avatar-element');
  return {
    username,
    bio,
    hasImg: !!img,
    letter: img ? '' : t(letterEl).slice(0, 2),
    color: letterEl ? (letterEl.getAttribute('data-color') || '') : '',
  };
}
"""

# آواتارِ روبیکا یک blob محلیِ مرورگر است و بیرون از همان تب قابل استفاده نیست،
# پس روی canvas کوچک می‌شود و به data-URL تبدیل می‌شود تا در JSON ذخیره شود.
_JS_AVATAR_DATA_URL = r"""
async () => {
  const img = document.querySelector(
    '#column-right .profile-avatars img, #column-right .avatar-element img, ' +
    '#column-center .chat-info .avatar-element img');
  if (!img || !img.src) return '';
  try {
    if (!img.complete) {
      await new Promise(res => { img.onload = res; img.onerror = res; setTimeout(res, 3000); });
    }
    const size = 96;
    const cv = document.createElement('canvas');
    cv.width = size; cv.height = size;
    const ctx = cv.getContext('2d');
    ctx.drawImage(img, 0, 0, size, size);
    return cv.toDataURL('image/jpeg', 0.82);
  } catch (e) {
    return '';
  }
}
"""

_JS_COUNT_POSTS = r"""
() => document.querySelectorAll('.bubbles .bubble.channel-post, .bubbles .bubble').length
"""

# اسکرولِ لیستِ پیام‌ها به بالا برای بارگذاریِ پست‌های قدیمی‌تر.
_JS_SCROLL_POSTS_UP = r"""
() => {
  const sc = document.querySelector('.bubbles .scrollable.scrollable-y')
         || document.querySelector('#column-center .scrollable.scrollable-y');
  if (!sc) return {ok: false};
  const before = sc.scrollTop;
  sc.scrollTop = Math.max(0, sc.scrollTop - sc.clientHeight * 2);
  return {ok: true, moved: Math.round(before - sc.scrollTop), atTop: sc.scrollTop <= 2};
}
"""

_JS_SCROLL_POSTS_BOTTOM = r"""
() => {
  const sc = document.querySelector('.bubbles .scrollable.scrollable-y')
         || document.querySelector('#column-center .scrollable.scrollable-y');
  if (sc) sc.scrollTop = sc.scrollHeight;
  return !!sc;
}
"""

# جمع‌آوریِ پست‌های کانال از حباب‌ها: متن/کپشن، بازدید، ری‌اکشن‌ها، ساعت و تاریخ.
# ساختارِ حباب همان چیزی است که موتورِ جستجو استخراج می‌کند (بازدید و ساعت هر دو
# داخلِ `[rb-message-time]`، ری‌اکشن‌ها در `.reaction.reaction-block`).
_JS_COLLECT_POSTS = r"""
() => {
  const txt = e => e ? ((e.innerText || e.textContent || '').trim()) : '';
  const monthRe = /(فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند)/;
  const relRe = /^(امروز|دیروز|شنبه|یکشنبه|دوشنبه|سه‌شنبه|سه شنبه|چهارشنبه|پنجشنبه|جمعه)/;
  const isDateText = t =>
    !!t && !/سنجاق|پین|pinned|حذف|ارتقا|عضو|ترک|تغییر/i.test(t) &&
    (monthRe.test(t) || relRe.test(t) || /\d{3,4}/.test(t) || /\d{1,2}[/\-]\d{1,2}/.test(t));

  // جداکننده‌های تاریخ به‌ترتیبِ سند، تا برای هر حباب نزدیک‌ترینِ پیشین پیدا شود.
  const seps = [...document.querySelectorAll(
    '.bubble.service, .bubble.is-date, .is-date .service-msg')]
    .map(s => ({el: s, text: (s.textContent || '').replace(/\s+/g, ' ').trim()}))
    .filter(s => isDateText(s.text));

  const bubbles = [...document.querySelectorAll('.bubbles .bubble')]
    .filter(b => !b.classList.contains('service') && !b.classList.contains('is-date'));

  const out = [];
  bubbles.forEach((b, i) => {
    b.setAttribute('data-basir-post', String(i));

    const textEl = b.querySelector(
      '[rb-message-text], .message, .text-content, .translatable-message');
    let content = '';
    if (textEl) {
      const clone = textEl.cloneNode(true);
      clone.querySelectorAll('.time, [rb-message-time], .reactions, .reactions-block')
        .forEach(e => e.remove());
      content = (clone.innerText || clone.textContent || '').trim();
    }

    const reactions = [...b.querySelectorAll(
      '.reactions .reaction.reaction-block, .reactions-block .reaction.reaction-block')].map(rc => {
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

    let views = '', time = '';
    const timeRoot = b.querySelector('[rb-message-time], .time');
    if (timeRoot) {
      const inner = timeRoot.querySelector('.inner') || timeRoot;
      const spans = [...inner.querySelectorAll('span')].reverse();
      const ts = spans.find(s => /\d{1,2}:\d{2}/.test(s.textContent));
      time = ts ? ts.textContent.trim() : '';
      if (timeRoot.querySelector('.rbico-channelviews') || b.querySelector('.rbico-channelviews')) {
        let t2 = (inner.textContent || '').replace(/\s+/g, ' ').trim();
        if (time) t2 = t2.replace(time, ' ');
        const vm = t2.match(/[\d\u06F0-\u06F9][\d\u06F0-\u06F9.,]*\s?[KMkm]?/);
        views = vm ? vm[0].replace(/\s+/g, '').trim() : '';
      }
    }

    let date = '';
    for (const s of seps) {
      if (s.el.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) date = s.text;
      else break;
    }

    const isMedia = /\b(photo|video|document|audio|voice|round)\b/.test(b.className);
    out.push({idx: i, content, reactions, views, time, date,
              is_media: isMedia, has_views: !!views});
  });
  return out;
}
"""


# ══════════════════════════════════════════════════════════════════════════════
# باز کردن کانال
# ══════════════════════════════════════════════════════════════════════════════

async def _goto_app(page) -> None:
    """وب‌اپ را باز می‌کند و از احرازشده بودنِ session مطمئن می‌شود."""
    await page.goto(RUBIKA_URL, wait_until="domcontentloaded", timeout=60_000)
    if not await _first_selector(page, LOGGED_IN_SELECTORS, timeout=25_000):
        raise RuntimeError("session اکانت روبیکا منقضی شده — دوباره وارد شوید.")


async def _search_channel_rows(page, username: str) -> list[dict]:
    """
    آیدی را در باکسِ جستجو تایپ می‌کند و ردیف‌های کانالِ نتیجه را برمی‌گرداند.

    جستجوی روبیکا زمانِ پاسخِ نامنظمی دارد (گاهی چند ثانیه)، پس با نمونه‌برداری
    صبر می‌کنیم و نه با خوابِ ثابت.
    """
    box = await _first_selector(page, SEARCH_SELECTORS, timeout=20_000)
    if box is None:
        raise RuntimeError("باکس جستجوی روبیکا پیدا نشد.")
    await box.click()
    await box.fill("")
    await asyncio.sleep(0.3)
    await box.type(f"@{username}", delay=60)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + CHANNEL_SEARCH_TIMEOUT_MS / 1000
    rows: list[dict] = []
    while True:
        try:
            rows = await page.evaluate(_JS_MARK_CHANNEL_ROWS)
        except Exception:
            rows = []
        if rows:
            return rows
        if loop.time() >= deadline:
            return []
        await asyncio.sleep(POSTS_POLL_MS / 1000)


async def _wait_posts_loaded(page, timeout_ms: int) -> int:
    """صبر می‌کند تا حباب‌های چت لود شوند و تعدادشان را برمی‌گرداند."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        try:
            n = await page.evaluate(_JS_COUNT_POSTS)
        except Exception:
            n = 0
        if n:
            return n
        if loop.time() >= deadline:
            return 0
        await asyncio.sleep(POSTS_POLL_MS / 1000)


async def _open_profile_panel(page) -> dict:
    """
    پنلِ «اطلاعات کانال» را با کلیک روی هدرِ چت باز می‌کند و آیدی/بیو/آواتار را
    می‌خواند. اگر پنل باز نشود، دیکشنریِ خالی برمی‌گردد (کارت بدونِ آیدی ساخته
    نمی‌شود؛ فراخواننده تصمیم می‌گیرد).
    """
    for sel in ('#column-center .sidebar-header.topbar .chat-info',
                '#column-center .chat-info',
                '#column-center .chat-info-container'):
        el = await page.query_selector(sel)
        if el is None:
            continue
        try:
            await el.click(timeout=5_000)
        except Exception:
            continue
        loop = asyncio.get_event_loop()
        deadline = loop.time() + PROFILE_PANEL_TIMEOUT_MS / 1000
        while loop.time() < deadline:
            await asyncio.sleep(POSTS_POLL_MS / 1000)
            try:
                info = await page.evaluate(_JS_PROFILE_INFO)
            except Exception:
                info = None
            if info and info.get("username"):
                return info
    return {}


async def _open_channel(page, username: str) -> dict:
    """
    کانال را با آیدی پیدا و باز می‌کند و اطلاعاتِ کارتش را برمی‌گرداند.

    ردیف‌های نتیجهٔ جستجو آیدی ندارند، پس ردیف‌ها به‌ترتیب باز می‌شوند و آیدیِ
    واقعی از پنلِ «اطلاعات کانال» بررسی می‌شود تا کانالِ هم‌نامِ اشتباهی انتخاب
    نشود.
    """
    username = normalize_username(username)
    if not username:
        raise RuntimeError("آیدی کانال معتبر نیست.")

    rows = await _search_channel_rows(page, username)
    if not rows:
        raise RuntimeError("کانالی با این لینک/آیدی پیدا نشد.")

    for row in rows[:5]:
        el = await page.query_selector(f'[data-basir-ch="{row["idx"]}"]')
        if el is None:
            continue
        try:
            await el.scroll_into_view_if_needed(timeout=4_000)
            await el.click(timeout=8_000)
        except Exception:
            continue

        if not await _wait_posts_loaded(page, CHANNEL_OPEN_TIMEOUT_MS):
            continue

        profile = await _open_profile_panel(page)
        found = normalize_username(profile.get("username", ""))
        if found and found.lower() != username.lower():
            continue

        header = await page.evaluate(_JS_CHAT_HEADER) or {}
        members = ""
        raw_header = (header.get("raw") or "")
        m = re.search(r'([\d\u06F0-\u06F9][\d\u06F0-\u06F9.,]*\s*[KMkm]?)\s*مشترک', raw_header)
        if m:
            members = f"{m.group(1).strip()} مشترک"
        elif row.get("subtitle"):
            members = row["subtitle"]

        avatar = ""
        try:
            avatar = await page.evaluate(_JS_AVATAR_DATA_URL) or ""
        except Exception:
            avatar = ""

        title = (header.get("title") or row.get("title") or username).strip()
        return {
            "username":      found or username,
            "url":           f"https://rubika.ir/{found or username}",
            "title":         title,
            "bio":           emojify((profile.get("bio") or "").strip()),
            "members":       members,
            "members_count": _parse_count(members),
            "avatar_url":    avatar,
            "avatar_letter": (profile.get("letter") or title[:1] or "?").strip(),
            "avatar_color":  profile.get("color", ""),
        }

    raise RuntimeError("کانالی با این آیدی باز نشد.")


# ══════════════════════════════════════════════════════════════════════════════
# اطلاعاتِ کارتِ کانال
# ══════════════════════════════════════════════════════════════════════════════

def _ready_account() -> dict:
    ready = [a for a in acc_store.load_accounts()
             if profile_has_data(a.get("user_data_dir", ""))]
    if not ready:
        raise RuntimeError("هیچ اکانت آماده‌ای برای کاوش موجود نیست.")
    return ready[0]


async def _with_account_page(fn):
    """
    یک context دائمیِ روبیکا با اکانتِ آماده باز می‌کند، `fn(page)` را اجرا
    می‌کند و در پایان می‌بندد. قفلِ اکانت جلوی استفادهٔ هم‌زمان از یک پروفایل
    (خطای SingletonLock کرومیوم) را می‌گیرد.
    """
    account = _ready_account()
    async with _get_account_lock(account["id"]):
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=account["user_data_dir"],
                headless=True,
                args=_CHROME_ARGS,
                user_agent=_USER_AGENT,
                viewport={"width": 1400, "height": 900},
                locale="fa-IR",
                timezone_id="Asia/Tehran",
                permissions=["clipboard-read", "clipboard-write"],
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await _goto_app(page)
                return await fn(page)
            finally:
                try:
                    await ctx.close()
                except Exception:
                    pass


def run_sync(coro, timeout: float = 1_800):
    """
    اجرای یک coroutine کاوش روی همان event-loopِ پس‌زمینهٔ job_runner و
    منتظر ماندن برای نتیجه (برای فراخوانی از درونِ request های Flask).

    حتماً باید همین یک loop باشد و نه asyncio.runِ تازه؛ وگرنه قفلِ per-account
    (که جلوی باز شدنِ هم‌زمانِ یک پروفایلِ Chrome توسط جستجو و کاوش را
    می‌گیرد) بینِ loop‌های مختلف بی‌اثر می‌شود و کرومیوم با خطای
    SingletonLock بالا نمی‌آید.
    """
    loop = job_runner._ensure_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)


async def fetch_channel_info(raw: str) -> dict:
    """اطلاعاتِ کانال را از داخلِ وب‌اپِ احرازشدهٔ روبیکا می‌خواند."""
    async def run(page):
        try:
            return await _open_channel(page, raw)
        except RuntimeError:
            return {}
    return await _with_account_page(run)


async def fetch_channel_info_bulk(raws: list[str]) -> list[dict]:
    """
    اطلاعاتِ چند کانال را در یک context (و به‌صورتِ ترتیبی) می‌خواند.

    برخلافِ بله که صفحهٔ عمومی داشت و می‌شد چند تب را هم‌زمان باز کرد، اینجا
    همه‌چیز داخلِ یک وب‌اپِ احرازشده انجام می‌شود و باز کردنِ هم‌زمانِ چند چت در
    یک session نتایج را قاطی می‌کند؛ پس ترتیبی پیش می‌رویم.
    """
    async def run(page):
        out: list[dict] = []
        for raw in raws:
            entry = {"raw": raw, "info": None, "error": ""}
            try:
                info = await _open_channel(page, raw)
                if info and info.get("username"):
                    entry["info"] = info
                else:
                    entry["error"] = "کانالی با این لینک/آیدی پیدا نشد."
            except RuntimeError as exc:
                entry["error"] = str(exc)
            except Exception as exc:
                entry["error"] = f"خطا در دریافت اطلاعات: {exc}"
            out.append(entry)
        return out
    return await _with_account_page(run)


# ══════════════════════════════════════════════════════════════════════════════
# بررسیِ پست‌های کانال
# ══════════════════════════════════════════════════════════════════════════════

async def _load_last_posts(page, num_posts: int) -> list[dict]:
    """
    به انتهای لیست می‌رود و در صورت نیاز به بالا اسکرول می‌کند تا حداقل
    `num_posts` پست بارگذاری شود، سپس n پستِ آخر را برمی‌گرداند.
    """
    try:
        await page.evaluate(_JS_SCROLL_POSTS_BOTTOM)
    except Exception:
        pass
    await asyncio.sleep(1.0)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + POSTS_LOAD_TIMEOUT_MS / 1000
    posts: list[dict] = []
    stale = 0
    while True:
        try:
            posts = await page.evaluate(_JS_COLLECT_POSTS)
        except Exception:
            posts = []
        if len(posts) >= num_posts or loop.time() >= deadline:
            break

        before = len(posts)
        try:
            res = await page.evaluate(_JS_SCROLL_POSTS_UP)
        except Exception:
            res = {"ok": False}
        await asyncio.sleep(1.2)

        try:
            after = len(await page.evaluate(_JS_COLLECT_POSTS))
        except Exception:
            after = before
        if after <= before and (not res.get("ok") or res.get("atTop")):
            stale += 1
            if stale >= 3:
                break            # ابتدای کانال — بیشتر از این پستی وجود ندارد
        else:
            stale = 0

    # پست‌های آخر (جدیدترین‌ها) و به‌ترتیبِ زمانی
    return posts[-num_posts:] if num_posts else posts


async def _post_link(page, idx: int) -> str:
    """
    لینکِ عمومیِ یک پست را با کلیک‌راست → «کپی کردن لینک پیام» می‌گیرد.

    کلیک روی آیتمِ منو باید کلیکِ واقعیِ Playwright باشد (نه JS)، چون نوشتنِ
    کلیپ‌بوردِ روبیکا به user-gesture معتبر نیاز دارد. مقدارِ کهنهٔ کلیپ‌بورد هم
    پذیرفته نمی‌شود تا لینکِ پستِ قبلی به این پست نسبت داده نشود.
    """
    bubble = await page.query_selector(f'[data-basir-post="{idx}"] .bubble-content')
    if bubble is None:
        bubble = await page.query_selector(f'[data-basir-post="{idx}"]')
    if bubble is None:
        return ""
    try:
        await bubble.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    try:
        if not await bubble.is_visible():
            return ""
        await bubble.click(button="right", timeout=4_000)
    except Exception:
        return ""

    item = None
    try:
        item = await page.wait_for_selector(
            '.btn-menu-item.rbico-link', timeout=MENU_TIMEOUT_MS, state="visible")
    except Exception:
        item = None
    if item is None:
        await _close_context_menu(page)
        return ""

    prev = await _read_clipboard(page)
    try:
        await item.click(timeout=4_000)
    except Exception:
        await _close_context_menu(page)
        return ""

    loop = asyncio.get_event_loop()
    deadline = loop.time() + LINK_TIMEOUT_MS / 1000
    while True:
        await asyncio.sleep(0.15)
        link = await _read_clipboard(page)
        if link and 'rubika' in link and link != prev:
            return link.strip()
        if loop.time() >= deadline:
            return ""


def _jalali_datetime(date_txt: str, time_txt: str) -> str:
    """«jy/jm/jd HH:MM» — همان فرمتِ شمسیِ عادیِ بخشِ جستجو."""
    greg = parse_rubika_date(date_txt) if date_txt else None
    if greg is None:
        return (time_txt or "").strip()
    jy, jm, jd = _gregorian_to_jalali(greg.year, greg.month, greg.day)
    return f"{jy}/{jm:02d}/{jd:02d} {time_txt}".strip()


async def analyze_channel(username: str, num_posts: int) -> dict:
    """
    n پستِ آخرِ کانال را بررسی می‌کند و آمار را برمی‌گرداند: بازدید، ری‌اکشن‌ها،
    میانگین‌ها و نرخ تعامل. بازدید و ری‌اکشن مستقیماً از حبابِ پست خوانده می‌شود
    (روبیکا هر دو را همان‌جا نشان می‌دهد)، پس نیازی به بازکردنِ صفحهٔ هر پست نیست.
    """
    username = normalize_username(username)
    num_posts = max(1, min(EXPLORE_MAX_POSTS, int(num_posts or 1)))

    async def run(page):
        info = await _open_channel(page, username)
        raw_posts = await _load_last_posts(page, num_posts)

        posts: list[dict] = []
        for p in raw_posts:
            content = emojify((p.get("content") or "").strip())
            reactions = p.get("reactions", []) or []
            for r in reactions:
                r["emoji"] = emojify((r.get("emoji") or "").strip())
            total_reactions = sum(_parse_count(r.get("count", "0")) for r in reactions)
            link = await _post_link(page, p["idx"])
            posts.append({
                "link":            link,
                "content":         content or ("(رسانه بدون متن)" if p.get("is_media") else ""),
                "views":           p.get("views", ""),
                "views_count":     _parse_count(p.get("views", "")),
                "reactions":       reactions,
                "total_reactions": total_reactions,
                "datetime_full":   _jalali_datetime(p.get("date", ""), p.get("time", "")),
            })
        return info, posts

    info, posts = await _with_account_page(run)
    return _summarize(info, username, num_posts, posts)


def _summarize(info: dict, username: str, requested: int, posts: list[dict]) -> dict:
    n = len(posts)
    total_views = sum(p["views_count"] for p in posts)
    total_reactions = sum(p["total_reactions"] for p in posts)
    avg_views = round(total_views / n, 1) if n else 0
    avg_reactions = round(total_reactions / n, 1) if n else 0
    # نرخ تعامل = مجموع ری‌اکشن‌ها ÷ مجموع بازدیدها (درصد)
    engagement = round((total_reactions / total_views) * 100, 2) if total_views else 0
    return {
        "username":        (info or {}).get("username", "") or username,
        "title":           (info or {}).get("title", ""),
        "members":         (info or {}).get("members", ""),
        "requested":       requested,
        "posts_analyzed":  n,
        "total_views":     total_views,
        "total_reactions": total_reactions,
        "avg_views":       avg_views,
        "avg_reactions":   avg_reactions,
        "engagement_rate": engagement,
        "posts":           posts,
    }
