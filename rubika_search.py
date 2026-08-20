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

from config import (
    RUBIKA_URL,
    SEARCH_TIMEOUT_MS,
    MAX_CONCURRENT,
    RESULTS_TIMEOUT_MS,
    RESULTS_SETTLE_MS,
    RESULTS_POLL_MS,
    QUERY_RETRIES,
    QUERY_RETRY_AFTER_MS,
    MESSAGE_LOAD_TIMEOUT_MS,
    MESSAGE_POLL_MS,
    EXTRACT_ATTEMPTS,
    LINK_TIMEOUT_MS,
    MENU_TIMEOUT_MS,
)

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

# ── نگاشتِ نام‌کوتاهِ اموجی → کاراکترِ واقعیِ یونیکد ────────────────────────────
# رابیکا (مثل خیلی از وب‌اپ‌های مبتنیِ tweb) برای اموجی‌های سفارشی/ری‌اکشن‌ها به‌جای
# کاراکترِ واقعی، فقط یک نامِ کوتاه (مثلاً «heart» یا «:red_circle:») در DOM
# می‌گذارد — همان چیزی که هنگامِ استخراجِ متن (textContent) به‌جای خودِ اموجی
# گرفته می‌شود. این دیکشنری رایج‌ترین نام‌کوتاه‌ها (هم‌راستا با استانداردِ
# Slack/Discord/GitHub) را به کاراکترِ واقعیِ اموجی تبدیل می‌کند.
_EMOJI_SHORTCODES: dict[str, str] = {
    # لبخند / صورت‌ها
    "grinning": "😀", "smiley": "😃", "smile": "😄", "grin": "😁",
    "laughing": "😆", "satisfied": "😆", "sweat_smile": "😅", "rofl": "🤣",
    "rolling_on_the_floor_laughing": "🤣", "joy": "😂", "slightly_smiling_face": "🙂",
    "upside_down_face": "🙃", "wink": "😉", "blush": "😊", "innocent": "😇",
    "smiling_face_with_three_hearts": "🥰", "heart_eyes": "😍", "star_struck": "🤩",
    "kissing_heart": "😘", "kissing": "😗", "kissing_smiling_eyes": "😙",
    "kissing_closed_eyes": "😚", "yum": "😋", "stuck_out_tongue": "😛",
    "stuck_out_tongue_winking_eye": "😜", "zany_face": "🤪",
    "stuck_out_tongue_closed_eyes": "😝", "money_mouth_face": "🤑", "hugs": "🤗",
    "hand_over_mouth": "🤭", "shushing_face": "🤫", "thinking": "🤔",
    "zipper_mouth_face": "🤐", "raised_eyebrow": "🤨", "neutral_face": "😐",
    "expressionless": "😑", "no_mouth": "😶", "smirk": "😏", "unamused": "😒",
    "roll_eyes": "🙄", "grimacing": "😬", "lying_face": "🤥", "relieved": "😌",
    "pensive": "😔", "sleepy": "😪", "drooling_face": "🤤", "sleeping": "😴",
    "mask": "😷", "face_with_thermometer": "🤒", "face_with_head_bandage": "🤕",
    "nauseated_face": "🤢", "vomiting_face": "🤮", "sneezing_face": "🤧",
    "hot_face": "🥵", "cold_face": "🥶", "woozy_face": "🥴", "dizzy_face": "😵",
    "exploding_head": "🤯", "cowboy_hat_face": "🤠", "partying_face": "🥳",
    "sunglasses": "😎", "nerd_face": "🤓", "monocle_face": "🧐", "confused": "😕",
    "worried": "😟", "slightly_frowning_face": "🙁", "frowning_face": "☹️",
    "open_mouth": "😮", "hushed": "😯", "astonished": "😲", "flushed": "😳",
    "pleading_face": "🥺", "frowning": "😦", "anguished": "😧", "fearful": "😨",
    "cold_sweat": "😰", "disappointed_relieved": "😥", "cry": "😢", "sob": "😭",
    "scream": "😱", "confounded": "😖", "persevere": "😣", "disappointed": "😞",
    "sweat": "😓", "weary": "😩", "tired_face": "😫", "yawning_face": "🥱",
    "triumph": "😤", "rage": "😡", "pout": "😡", "angry": "😠", "cursing_face": "🤬",
    "smiling_imp": "😈", "imp": "👿", "skull": "💀", "skull_and_crossbones": "☠️",
    "hankey": "💩", "poop": "💩", "shit": "💩", "clown_face": "🤡", "japanese_ogre": "👹",
    "japanese_goblin": "👺", "ghost": "👻", "alien": "👽", "robot": "🤖",
    # قلب‌ها
    "heart": "❤️", "red_heart": "❤️", "orange_heart": "🧡", "yellow_heart": "💛",
    "green_heart": "💚", "blue_heart": "💙", "purple_heart": "💜",
    "black_heart": "🖤", "white_heart": "🤍", "brown_heart": "🤎",
    "broken_heart": "💔", "heavy_heart_exclamation": "❣️", "two_hearts": "💕",
    "revolving_hearts": "💞", "heartbeat": "💓", "heartpulse": "💗",
    "sparkling_heart": "💖", "cupid": "💘", "gift_heart": "💝",
    "heart_decoration": "💟", "mending_heart": "❤️‍🩹", "fire_heart": "❤️‍🔥",
    # دست‌ها / اشاره‌ها
    "clap": "👏", "raised_hands": "🙌", "open_hands": "👐", "handshake": "🤝",
    "pray": "🙏", "wave": "👋", "ok_hand": "👌", "pinched_fingers": "🤌",
    "pinching_hand": "🤏", "v": "✌️", "crossed_fingers": "🤞", "love_you_gesture": "🤟",
    "metal": "🤘", "call_me_hand": "🤙", "point_left": "👈", "point_right": "👉",
    "point_up_2": "👆", "point_down": "👇", "point_up": "☝️", "raised_hand": "✋",
    "thumbsup": "👍", "+1": "👍", "thumbsdown": "👎", "-1": "👎", "fist": "✊",
    "facepunch": "👊", "punch": "👊", "muscle": "💪", "writing_hand": "✍️",
    "nail_care": "💅", "selfie": "🤳",
    # نمادها/اشکال
    "red_circle": "🔴", "orange_circle": "🟠", "yellow_circle": "🟡",
    "green_circle": "🟢", "blue_circle": "🔵", "purple_circle": "🟣",
    "brown_circle": "🟤", "black_circle": "⚫", "white_circle": "⚪",
    "red_square": "🟥", "orange_square": "🟧", "yellow_square": "🟨",
    "green_square": "🟩", "blue_square": "🟦", "purple_square": "🟪",
    "brown_square": "🟫", "black_large_square": "⬛", "white_large_square": "⬜",
    "small_blue_diamond": "🔹", "small_orange_diamond": "🔸",
    "large_blue_diamond": "🔷", "large_orange_diamond": "🔶",
    "diamond_shape_with_a_dot_inside": "💠", "gem": "💎",
    "star": "⭐", "star2": "🌟", "sparkles": "✨", "boom": "💥", "collision": "💥",
    "fire": "🔥", "zap": "⚡", "cyclone": "🌀", "dizzy": "💫", "anger": "💢",
    "exclamation": "❗", "heavy_exclamation_mark": "❗", "question": "❓",
    "grey_exclamation": "❕", "grey_question": "❔", "bangbang": "‼️",
    "interrobang": "⁉️", "warning": "⚠️", "no_entry": "⛔",
    "checkmark": "✔️", "heavy_check_mark": "✔️", "white_check_mark": "✅",
    "x": "❌", "negative_squared_cross_mark": "❎", "100": "💯", "hundred": "💯",
    # سایر پرکاربرد
    "tada": "🎉", "confetti_ball": "🎊", "trophy": "🏆", "medal": "🏅",
    "eyes": "👀", "eye": "👁️", "tongue": "👅", "lips": "👄", "kiss": "💋",
    "speech_balloon": "💬", "thought_balloon": "💭", "zzz": "💤",
    "sun": "☀️", "sunny": "☀️", "moon": "🌙", "crescent_moon": "🌙",
    "cloud": "☁️", "rainbow": "🌈", "snowflake": "❄️", "droplet": "💧",
    "ocean": "🌊", "balloon": "🎈", "gift": "🎁", "bell": "🔔",
    "musical_note": "🎵", "notes": "🎶", "rocket": "🚀", "airplane": "✈️",
    "car": "🚗", "coffee": "☕", "pizza": "🍕", "hamburger": "🍔",
    "beer": "🍺", "beers": "🍻", "wine_glass": "🍷", "cake": "🎂",
    "birthday": "🎂", "rose": "🌹", "sunflower": "🌻", "four_leaf_clover": "🍀",
    "dog": "🐶", "cat": "🐱", "bird": "🐦", "dove": "🕊️", "unicorn": "🦄",
    "diamonds": "🔹", "bus": "🚌", "train": "🚆", "train2": "🚆",
    "bullettrain_side": "🚄", "metro": "🚇", "ship": "🚢", "taxi": "🚕",
    "hotel": "🏨", "ticket": "🎫", "calendar": "📅", "date": "📅",
    "pushpin": "📌", "round_pushpin": "📍", "link": "🔗", "memo": "📝",
    "newspaper": "📰", "camera": "📷", "video_camera": "📹", "movie_camera": "🎥",
    "telephone": "☎️", "phone": "📱", "iphone": "📱", "email": "✉️",
    "chart_with_upwards_trend": "📈", "chart_with_downwards_trend": "📉",
    "bar_chart": "📊", "money_with_wings": "💸", "dollar": "💵",
    "moneybag": "💰", "credit_card": "💳", "package": "📦", "bulb": "💡",
    "lock": "🔒", "key": "🔑", "mag": "🔍", "loudspeaker": "📢", "mega": "📣",
    "bookmark": "🔖", "books": "📚", "book": "📖", "clock": "🕐",
    "alarm_clock": "⏰", "hourglass": "⌛", "watch": "⌚", "globe": "🌐",
    "earth_asia": "🌏", "flag_ir": "🇮🇷", "ir": "🇮🇷",
}


def _shortcode_to_emoji(name: str) -> str | None:
    key = (name or "").strip().strip(":").strip().lower().replace("-", "_").replace(" ", "_")
    return _EMOJI_SHORTCODES.get(key)


def emojify(text: str) -> str:
    """
    نام‌کوتاه‌های اموجی که به‌جای خودِ اموجی از DOM استخراج شده‌اند (مثلاً
    «:red_circle:» یا حتی فقط «heart»/«fire» بدون دو نقطه) را به کاراکترِ
    واقعیِ اموجی تبدیل می‌کند. اگر نام‌کوتاه در دیکشنری نبود، فقط دو نقطه‌ها
    حذف می‌شوند تا حداقل شکلِ خام «:something:» به چشم نیاید.
    """
    if not text:
        return text

    def _replace_colon_form(m: re.Match) -> str:
        emoji = _shortcode_to_emoji(m.group(0))
        return emoji if emoji else m.group(0).strip(":")

    text = _SHORTCODE_RE.sub(_replace_colon_form, text)

    # حالتِ بدونِ دو نقطه (مثلاً ری‌اکشنی که alt/title متنش فقط «heart» است) —
    # فقط وقتی کلِ رشته دقیقاً یک نام‌کوتاهِ شناخته‌شده باشد تبدیل می‌شود، تا
    # کلماتِ عادیِ متنِ پیام (مثل جملاتی که تصادفاً شامل واژهٔ «fire» هستند)
    # دست‌نخورده بمانند.
    bare = _shortcode_to_emoji(text)
    if bare:
        return bare

    return text


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

# شمارشِ سریعِ نتایج (برای پُل‌زدن تا کاملِ شدنِ لیست، بدون هزینهٔ جمع‌آوریِ کامل)
_JS_COUNT_RESULTS = r"""
() => {
  const grp = document.querySelector('.search-group-messages');
  return grp ? grp.querySelectorAll('ul.chatlist > li').length : 0;
}
"""

# اسکرولِ ظرفِ نتایج برای تحریکِ لودِ تدریجی (اگر روبیکا صفحه‌بندی کند).
# ظرفِ درست، نزدیک‌ترین والدِ اسکرول‌شدنی است؛ مقدارِ برگشتی وضعیت اسکرول است.
_JS_SCROLL_RESULTS = r"""
(toEnd) => {
  const grp = document.querySelector('.search-group-messages');
  if (!grp) return {ok: false};
  let sc = null, el = grp;
  while (el && el !== document.body) {
    const st = getComputedStyle(el);
    if (/(auto|scroll)/.test(st.overflowY) && el.scrollHeight > el.clientHeight + 10) { sc = el; break; }
    el = el.parentElement;
  }
  if (!sc) {
    sc = grp.closest('.scrollable') ||
         document.querySelector('.search-super .scrollable') ||
         document.querySelector('.sidebar-search .scrollable');
  }
  if (!sc) return {ok: false};
  const before = sc.scrollTop;
  sc.scrollTop = toEnd ? sc.scrollHeight : 0;
  return {ok: true, moved: Math.round(sc.scrollTop) - Math.round(before),
          atEnd: sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 5};
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
  const textOf = b => {
    // علاوه بر پیامِ متنی، کپشنِ عکس/ویدیو هم باید تطبیق داده شود.
    const el = b.querySelector('[rb-message-text], .message, .text-content, .translatable-message');
    return el ? el.textContent : '';
  };
  let bubble = null;
  if (key) {
    for (const b of bubbles) {
      const t = norm(textOf(b));
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

  // تاریخِ دقیقِ پیام: نزدیک‌ترین جداکنندهٔ تاریخِ خدماتی که پیش از این حباب آمده
  // (مثلاً «دوشنبه، ۲۹ تیر ۱۴۰۵»). فقط پیام‌های خدماتیِ «تاریخ» را می‌پذیریم؛
  // پیام‌های خدماتیِ دیگر مثل «یک پیام سنجاق شد» نباید به‌جای تاریخ گرفته شوند.
  let date = '';
  const monthRe = /(فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند)/;
  const relRe = /^(امروز|دیروز|شنبه|یکشنبه|دوشنبه|سه‌شنبه|سه شنبه|چهارشنبه|پنجشنبه|جمعه)/;
  const isDateText = t =>
    !!t && !/سنجاق|پین|pinned|حذف|ارتقا|عضو|ترک|تغییر/i.test(t) &&
    (monthRe.test(t) || relRe.test(t) || /\d{3,4}/.test(t) || /\d{1,2}[/\-]\d{1,2}/.test(t));
  const svcs = [...document.querySelectorAll('.bubble.service, .bubble.is-date, .is-date .service-msg, .bubble.service .service-msg')];
  for (const s of svcs) {
    // اگر حباب بعد از s باشد، s پیش از حباب است → کاندید (آخرین کاندیدِ تاریخ‌مانند = نزدیک‌ترین)
    if (s.compareDocumentPosition(bubble) & Node.DOCUMENT_POSITION_FOLLOWING) {
      const t = (s.textContent || '').replace(/\s+/g, ' ').trim();
      if (isDateText(t)) date = t;
    } else {
      break;
    }
  }

  return {content, reactions, views, time, date};
}
"""

# گزینهٔ «کپی کردن لینک پیام» در منوی کلیک‌راست روبیکا را کلیک می‌کند (کانال‌ها).
# در DOM واقعیِ روبیکا این آیتم کلاسِ .rbico-link دارد و متنش «کپی کردن لینک پیام»
# است (نه «رونوشت لینک»). اول با کلاس، بعد با متن پیدا می‌شود.
_JS_COPY_LINK = r"""
() => {
  const wanted = ['کپی کردن لینک پیام', 'کپی لینک پیام', 'لینک پیام',
                  'رونوشت لینک', 'کپی لینک', 'کپی کردن لینک',
                  'Copy Link', 'Copy Message Link'];
  const items = [...document.querySelectorAll('.btn-menu-item, .menu-item, [role=menuitem]')];
  // اولویت با کلاسِ صریحِ rbico-link
  for (const it of items) {
    if (it.classList && it.classList.contains('rbico-link')) { it.click(); return true; }
  }
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


async def _read_clipboard(page) -> str:
    try:
        return (await page.evaluate(
            "async () => { try { return await navigator.clipboard.readText(); } "
            "catch (e) { return ''; } }")) or ""
    except Exception:
        return ""


async def _close_context_menu(page) -> None:
    """
    منوی کلیک‌راست را می‌بندد.

    منوی بازمانده روی صفحه می‌نشیند و کلیکِ نتیجهٔ بعدی را مسدود می‌کند، پس
    بی‌قیدوشرط Escape می‌زنیم. اگر این کار پنلِ جستجو را هم ببندد، حلقهٔ
    اصلی لیست را با `expect` و بدونِ مرحلهٔ تثبیت، سریع بازمی‌گرداند.
    """
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


async def _extract_message_details(page, mid: str, snippet: str,
                                   timeout_ms: int = MESSAGE_LOAD_TIMEOUT_MS,
                                   want_link: bool = True) -> dict:
    """
    بعد از باز شدن چت و پرش به پیام، محتوای کامل، ری‌اکشن‌ها، بازدید و لینک را
    مستقیماً از حبابِ پیام استخراج می‌کند.

    تا `timeout_ms` صبر می‌کند که حبابِ منطبق با اسنیپت لود شود، ولی به‌محضِ
    آماده شدن ادامه می‌دهد (نمونه‌برداریِ سریع) — هم پرحوصله و هم سریع.
    فیلدِ `matched` نشان می‌دهد محتوای استخراج‌شده با اسنیپتِ نتیجه هم‌خوان بود
    یا نه؛ تصمیمِ تلاشِ مجدد با فراخواننده است تا هیچ نتیجه‌ای از قلم نیفتد.
    """
    details = {
        'full_content': '',
        'reactions': [],
        'total_reactions': 0,
        'views': '',
        'datetime_full': '',
        'msg_date': '',
        'message_link': '',
        'matched': False,
    }

    # پُل‌زدنِ سریع تا لودِ حبابِ منطبق؛ اولویت با حبابی که با اسنیپت می‌خواند.
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    data = None
    best = None
    poll = MESSAGE_POLL_MS / 1000
    while True:
        try:
            data = await page.evaluate(_JS_EXTRACT_BUBBLE, snippet)
        except Exception:
            data = None

        if data:
            content = (data.get('content') or '').strip()
            if content and (not snippet or _snippet_matches(snippet, content)):
                break                      # حبابِ درست لود شد → ادامه
            if content or data.get('reactions'):
                best = data              # کاندیدِ ضعیف؛ اگر بهتری نیامد همین

        if asyncio.get_event_loop().time() >= deadline:
            data = data if (data and (data.get('content') or '').strip()) else best
            break
        await asyncio.sleep(poll)

    if not data:
        print(f"[Basir] حباب پیام پیدا نشد: {snippet[:40]}")
        return details

    full_content = (data.get('content') or '').strip()
    details['matched'] = bool(
        full_content and (not snippet or _snippet_matches(snippet, full_content)))

    details['full_content'] = emojify(full_content or snippet)
    reactions = data.get('reactions', []) or []
    for r in reactions:
        r['emoji'] = emojify((r.get('emoji') or '').strip())
    details['reactions'] = reactions
    details['total_reactions'] = sum(_parse_count(r.get('count', '0')) for r in reactions)
    details['views'] = data.get('views', '') or ''

    # تاریخ و زمانِ دقیقِ پیام — به فرمتِ شمسیِ عادی مثل بله/ایتا: «jy/jm/jd HH:MM»
    msg_time = (data.get('time', '') or '').strip()
    msg_date = (data.get('date', '') or '').strip()
    details['msg_date'] = msg_date
    greg = parse_rubika_date(msg_date) if msg_date else None
    if greg is not None:
        jy, jm, jd = _gregorian_to_jalali(greg.year, greg.month, greg.day)
        jalali = f"{jy}/{jm:02d}/{jd:02d}"
        details['datetime_full'] = f"{jalali} {msg_time}".strip()
    else:
        details['datetime_full'] = msg_time

    # لینکِ پیام (کانال‌ها) از منوی کلیک‌راست → گزینهٔ «کپی کردن لینک پیام».
    # مهم: باید با کلیکِ واقعیِ Playwright روی آیتم منو زده شود، نه با
    # element.click() در JS؛ چون نوشتنِ کلیپ‌بوردِ روبیکا به یک user-gesture
    # معتبر نیاز دارد و کلیکِ برنامه‌ایِ JS آن را بی‌صدا مسدود می‌کند.
    try:
        if not want_link:
            return details
        bubble = await page.query_selector('.bubble[data-basir-target="1"] .bubble-content')
        if bubble is None:
            bubble = await page.query_selector('.bubble[data-basir-target="1"]')
        if bubble is not None and await bubble.is_visible():
            # منو با تأخیرِ متغیر باز می‌شود؛ با صبرِ فعال (نه sleep ثابت) و یک
            # تلاشِ مجدد، کلیک‌راستِ نگرفته هم جبران می‌شود.
            item = None
            for rc_try in range(2):
                try:
                    await bubble.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                try:
                    await bubble.click(button="right", timeout=4000)
                except Exception:
                    break
                try:
                    item = await page.wait_for_selector(
                        '.btn-menu-item.rbico-link', timeout=MENU_TIMEOUT_MS, state="visible")
                except PwTimeout:
                    item = None
                if item is None:
                    for it in await page.query_selector_all(
                            '.btn-menu-item, .menu-item, [role=menuitem]'):
                        try:
                            t = (await it.text_content() or '').strip()
                            vis = await it.is_visible()
                        except Exception:
                            t, vis = '', False
                        if vis and ('لینک پیام' in t or 'کپی کردن لینک' in t
                                    or 'رونوشت لینک' in t):
                            item = it
                            break
                if item is not None:
                    break
                # منو باز نشد یا خالی بود → ببند و یک بار دیگر امتحان کن
                if rc_try == 0:
                    await _close_context_menu(page)
                    await asyncio.sleep(0.3)
            if item is not None:
                # مقدارِ قبلیِ کلیپ‌بورد (لینکِ پیامِ قبلی) نگه داشته می‌شود؛ اگر
                # کپی نگیرد، همان مقدارِ کهنه خوانده و به‌اشتباه به این پیام
                # نسبت داده می‌شود. پس فقط مقدارِ تازه پذیرفته می‌شود.
                prev = await _read_clipboard(page)
                await item.click(timeout=4000)
                link = ""
                link_deadline = asyncio.get_event_loop().time() + LINK_TIMEOUT_MS / 1000
                while True:
                    await asyncio.sleep(0.15)
                    link = await _read_clipboard(page)
                    if link and 'rubika' in link and link != prev:
                        break
                    if asyncio.get_event_loop().time() >= link_deadline:
                        break
                if link and 'rubika' in link and link != prev:
                    details['message_link'] = link.strip()
            else:
                await _close_context_menu(page)
    except Exception:
        await _close_context_menu(page)

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
    # حذفِ پیشوندِ روزِ هفته + ویرگول (مثلاً «دوشنبه، ۲۹ تیر ۱۴۰۵» → «۲۹ تیر ۱۴۰۵»)
    s = re.sub(
        r'^\s*(شنبه|یکشنبه|دوشنبه|سه‌شنبه|سه\s*شنبه|چهارشنبه|پنجشنبه|جمعه)\s*[،,]?\s*',
        '', s).strip()
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
# ثبتِ کوئری و برداشتِ کاملِ نتایج
# ══════════════════════════════════════════════════════════════════════════════
def _row_key(row: dict) -> tuple[str, str, str]:
    """کلیدِ یکتای یک ردیفِ نتیجه (عنوان، اسنیپت، زمان)."""
    return ((row.get('title') or '').strip(),
            (row.get('subtitle') or '').strip(),
            (row.get('time') or '').strip())


async def _submit_query(page, search_query: str, typed: bool = False) -> bool:
    """
    کوئری را در باکسِ جستجوی کناری می‌نویسد و جستجو را اجرا می‌کند.

    در تلاشِ مجدد (typed=True) حرف‌به‌حرف تایپ می‌شود؛ رویدادهای واقعیِ
    کی‌بورد کمپوننتِ جستجوی Angular را مطمئن‌تر دوباره تحریک می‌کنند.
    """
    box = await _first_selector(page, SEARCH_SELECTORS, timeout=12_000)
    if not box:
        return False
    try:
        await box.click()
        await box.fill("")
        if typed:
            await asyncio.sleep(0.5)
            await box.type(search_query, delay=60)
        else:
            await box.fill(search_query)
        await page.keyboard.press("Enter")
        return True
    except Exception as e:
        print(f"[Basir] ثبت کوئری ناموفق: {e}")
        return False


async def _harvest_results(page, search_query: str, expect: int = 0) -> list[dict]:
    """
    همهٔ ردیف‌های گروهِ «پیام‌ها» را برمی‌دارد.

    سرورِ روبیکا گاهی چند ثانیه و گاهی بیش از ۲۰ ثانیه طول می‌کشد؛ پس:
      • تا RESULTS_TIMEOUT_MS برای اولین نتیجه صبر می‌کنیم (نه چند ثانیه)،
      • اگر تا QUERY_RETRY_AFTER_MS چیزی نیامد، کوئری را دوباره ثبت می‌کنیم،
      • بعد از آمدنِ نتایج، تا وقتی تعدادشان RESULTS_SETTLE_MS بی‌تغییر نماند
        برداشت نمی‌کنیم تا لیست ناقص برداشته نشود،
      • با اسکرول تا انتها و برگشت، لودِ تدریجیِ احتمالی هم تحریک می‌شود.

    `expect` تعدادِ ردیفِ انتزاری است (برای بازیابیِ لیستِ قبلاً برداشته‌شده)؛
    با رسیدن به این تعداد، مرحلهٔ تثبیت رد می‌شود تا وقت تلف نشود.
    """
    loop = asyncio.get_event_loop()
    poll = RESULTS_POLL_MS / 1000
    deadline = loop.time() + RESULTS_TIMEOUT_MS / 1000
    retry_at = loop.time() + QUERY_RETRY_AFTER_MS / 1000
    retries_left = QUERY_RETRIES

    count = 0
    while True:
        try:
            count = await page.evaluate(_JS_COUNT_RESULTS)
        except Exception:
            count = 0
        if expect and count >= expect:
            return await _collect_rows(page)
        if count:
            break
        now = loop.time()
        if now >= deadline:
            print("[Basir] هیچ نتیجه‌ای در مهلتِ مقرر نیامد")
            return []
        if now >= retry_at and retries_left > 0:
            retries_left -= 1
            print(f"[Basir] نتیجه‌ای نیامد — ثبتِ مجددِ کوئری ({QUERY_RETRIES - retries_left})")
            await _submit_query(page, search_query, typed=True)
            retry_at = loop.time() + QUERY_RETRY_AFTER_MS / 1000
        await asyncio.sleep(poll)

    print(f"[Basir] اولین نتایج آمد ({count}) — صبر تا کامل شدنِ لیست")

    # تثبیت: تا وقتی تعداد ثابت نماند، هنوز در حال لود است.
    settle = RESULTS_SETTLE_MS / 1000
    stable_since = loop.time()
    scrolled_end = False
    while True:
        await asyncio.sleep(poll)
        try:
            now_count = await page.evaluate(_JS_COUNT_RESULTS)
        except Exception:
            now_count = count
        if now_count != count:
            count = now_count
            stable_since = loop.time()
            scrolled_end = False
            continue
        # تعداد ثابت است؛ یک بار تا انتها اسکرول کن تا لودِ تدریجی تحریک شود.
        if not scrolled_end and loop.time() - stable_since > settle / 2:
            try:
                await page.evaluate(_JS_SCROLL_RESULTS, True)
            except Exception:
                pass
            scrolled_end = True
            continue
        if loop.time() - stable_since >= settle:
            break
        if loop.time() >= deadline:
            break

    try:
        await page.evaluate(_JS_SCROLL_RESULTS, False)   # برگشت به بالای لیست
    except Exception:
        pass

    rows = await _collect_rows(page)
    print(f"[Basir] {len(rows)} نتیجه برداشت شد")
    return rows


async def _collect_rows(page) -> list[dict]:
    try:
        return await page.evaluate(_JS_COLLECT_RESULTS)
    except Exception as e:
        print(f"[Basir] خطای جمع‌آوری نتایج: {e}")
        return []


_JS_CLICK_ROW = r"""
(idx) => {
  const el = document.querySelector(`[data-basir-idx="${idx}"]`);
  if (!el) return false;
  (el.querySelector('.chatlist-chat, a, .row') || el).click();
  return true;
}
"""


async def _click_row(page, row: dict, expected_key: tuple[str, str, str]) -> bool:
    """
    ردیفِ نتیجه را کلیک می‌کند. چون Angular ممکن است لیست را بازسازی کند،
    ایندکس‌ها را تازه می‌گیریم و ردیف را با کلیدش پیدا می‌کنیم (نه ایندکسِ کهنه).

    ردیف‌های پایین‌ترِ لیست بیرونِ کادرِ دید هستند و Playwright آن‌ها را
    «نامرئی» می‌بیند؛ پس اول داخلِ دید اسکرول می‌شوند و اگر کلیکِ واقعی هم
    نگرفت، کلیکِ برنامه‌ای انجام می‌شود (اینجا user-gesture لازم نیست).
    """
    idx = row.get('idx')
    try:
        fresh = await page.evaluate(_JS_COLLECT_RESULTS)
    except Exception:
        fresh = []
    for fr in fresh:
        if _row_key(fr) == expected_key:
            idx = fr.get('idx')
            break
    if idx is None:
        return False

    el = await page.query_selector(f'[data-basir-idx="{idx}"]')
    if el is not None:
        try:
            await el.scroll_into_view_if_needed(timeout=4_000)
        except Exception:
            pass
        try:
            await el.click(timeout=5_000)
            return True
        except Exception:
            pass

    try:
        if await page.evaluate(_JS_CLICK_ROW, idx):
            return True
    except Exception as e:
        print(f"[Basir] کلیک روی نتیجه ناموفق: {e}")
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
                        headless=True,
                        args=_CHROME_ARGS,
                        user_agent=_USER_AGENT,
                        viewport={"width": 1280, "height": 800},
                        locale="fa-IR",
                        timezone_id="Asia/Tehran",
                        permissions=["clipboard-read", "clipboard-write"],
                    )

                    pages = ctx.pages
                    page = pages[0] if pages else await ctx.new_page()

                    await page.goto(RUBIKA_URL, wait_until="domcontentloaded", timeout=60_000)

                    # بررسی لاگین بودن — با صبرِ کافی برای بالا آمدنِ اپِ Angular،
                    # وگرنه اکانتِ سالم به‌اشتباه «منقضی» تشخیص داده و رد می‌شود.
                    logged_in = None
                    try:
                        logged_in = await page.wait_for_selector(
                            ", ".join(LOGGED_IN_SELECTORS), timeout=45_000, state="attached")
                    except PwTimeout:
                        logged_in = None
                    if not logged_in:
                        print(f"[Basir] «{account['name']}» session منقضی — skip")
                        return []

                    print(f"[Basir] «{account['name']}» — جستجو: {query}")

                    # عبارتِ واردشده داخلِ دو تا دابل‌کوتیشن در کادرِ جستجوی
                    # رابیکا تایپ می‌شود (نه خودِ عبارتِ خام) — طبق درخواستِ کاربر.
                    search_query = f'"{query}"'

                    # ── ۱) ثبتِ کوئری و برداشتِ کاملِ لیستِ نتایج ─────────────
                    # اول کلِ لیست برداشته می‌شود و بعد یکی‌یکی استخراج می‌شویم؛
                    # این‌طور کلیک‌کردن روی نتایج، لیست را ناقص نمی‌کند و هیچ
                    # ردیفی از قلم نمی‌افتد.
                    if not await _submit_query(page, search_query):
                        print(f"[Basir] باکس جستجو برای «{account['name']}» پیدا نشد")
                        return []
                    print("Started Searching ... ")

                    rows = await _harvest_results(page, search_query)

                    # ردیف‌های یکتا با حفظِ ترتیب (اسنیپتِ خالی قابلِ استخراج نیست)
                    targets: list[dict] = []
                    seen: set[tuple[str, str, str]] = set()
                    for row in rows:
                        key = _row_key(row)
                        if not key[1] or key in seen:
                            continue
                        seen.add(key)
                        targets.append(row)

                    total = len(targets)
                    print(f"[Basir] «{account['name']}» — {total} نتیجهٔ یکتا برای استخراج")

                    # ── ۲) استخراجِ جزئیاتِ هر نتیجه ──────────────────────────
                    MessageTemp = []
                    for n, row in enumerate(targets, 1):
                        title, snippet, rdate = _row_key(row)
                        msg_details = None

                        for attempt in range(1, EXTRACT_ATTEMPTS + 1):
                            if not await _click_row(page, row, (title, snippet, rdate)):
                                # فقط اگر لیست واقعاً پاک شده، کوئری را دوباره اجرا کن
                                try:
                                    still = await page.evaluate(_JS_COUNT_RESULTS)
                                except Exception:
                                    still = 0
                                if not still:
                                    await _submit_query(page, search_query)
                                    await _harvest_results(page, search_query, expect=total)
                                continue

                            # تلاشِ اول کوتاه‌تر است چون حبابِ درست معمولاً چند
                            # ثانیه‌ای می‌آید؛ اگر نیامد مشکل «چتِ اشتباه» است و
                            # کلیکِ مجدد چاره‌ساز است، نه صبرِ بیشتر. در تلاش‌های
                            # بعدی مهلتِ کامل داده می‌شود تا پیامِ کُند هم برسد.
                            msg_details = await _extract_message_details(
                                page, "", snippet,
                                timeout_ms=(MESSAGE_LOAD_TIMEOUT_MS if attempt > 1
                                            else max(12_000, MESSAGE_LOAD_TIMEOUT_MS // 2)),
                            )
                            if msg_details.get('matched'):
                                break
                            print(f"[Basir] تلاش {attempt}/{EXTRACT_ATTEMPTS} برای «{snippet[:30]}»")

                        if msg_details is None:
                            msg_details = {}

                        # هیچ نتیجه‌ای دور ریخته نمی‌شود؛ اگر استخراجِ کامل نشد،
                        # اسنیپتِ نتیجه به‌عنوان محتوا می‌ماند.
                        exact_date = (msg_details.get('msg_date') or '').strip()
                        newMessage = {
                            'Title':   title,
                            'Date':    exact_date or rdate,
                            'Content': snippet,
                            'full_content':    msg_details.get('full_content', '') or snippet,
                            'reactions':       msg_details.get('reactions', []),
                            'total_reactions': msg_details.get('total_reactions', 0),
                            'views':           msg_details.get('views', ''),
                            'datetime_full':   msg_details.get('datetime_full', ''),
                            'message_link':    msg_details.get('message_link', ''),
                        }
                        MessageTemp.append(newMessage)
                        print(f"[Basir] {n}/{total} «{title[:20]}» "
                              f"views={newMessage['views'] or '—'} "
                              f"reactions={newMessage['total_reactions']} "
                              f"{'✓' if msg_details.get('matched') else '~'}")

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

                            CorrectDate = parse_rubika_date(Message.get('Date'))
                            if CorrectDate is not None:
                                Shamsi = str(JalaliDate(CorrectDate))
                            else:
                                Shamsi = Message.get('Date') or "—"

                            results.append({
                                "channel_name": emojify(Message.get('Title')),
                                "message": emojify(Message.get('Content')),
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
