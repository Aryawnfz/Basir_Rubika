"""
هایلایتِ عبارتِ جستجوشده داخلِ متنِ پیام‌ها (صفحهٔ نتایج).

نکات:
  - تطبیق، فازی‌ست: تفاوتِ ی/ي/ى و ک/ك و فاصله‌های چندتایی نادیده گرفته
    می‌شود، چون محتوای اسکرِیپ‌شده و عبارتِ تایپ‌شدهٔ کاربر همیشه از یک
    نویسه‌گذاری نیستند.
  - چون خودِ متنِ اصلی (بدون نرمال‌سازی) برای تطبیق استفاده می‌شود، آفستِ
    match دقیقاً همان جایگاهِ واقعی در متنِ نمایش‌داده‌شده است — نیازی به
    نگاشتِ برگشتی نیست.
  - خروجی، Markup (HTML-safe) است: بخش‌های غیرِمطابق escape می‌شوند و فقط
    بخشِ مطابق داخلِ <mark> قرار می‌گیرد، تا هم از XSS جلوگیری شود و هم
    اسکیپِ خودکارِ Jinja دوباره آن را encode نکند.
"""
import re

from markupsafe import Markup, escape

_YE_VARIANTS = "یيى"
_KE_VARIANTS = "کك"


def _fuzzy_pattern(query: str) -> str:
    parts = []
    for ch in query:
        if ch in _YE_VARIANTS:
            parts.append(f"[{_YE_VARIANTS}]")
        elif ch in _KE_VARIANTS:
            parts.append(f"[{_KE_VARIANTS}]")
        elif ch.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _compile_query(query: str):
    q = (query or "").strip()
    if not q:
        return None
    try:
        return re.compile(_fuzzy_pattern(q), re.IGNORECASE | re.UNICODE)
    except re.error:
        return None


def highlight_query(text: str, query: str) -> Markup:
    """متنِ کامل را escape می‌کند و هر بخشِ منطبق با query را داخلِ <mark> می‌گذارد."""
    text = text or ""
    pattern = _compile_query(query)
    if pattern is None:
        return Markup(escape(text))

    pieces = []
    last = 0
    for m in pattern.finditer(text):
        if m.start() == m.end():
            continue
        pieces.append(escape(text[last:m.start()]))
        pieces.append(Markup("<mark class=\"hl-match\">"))
        pieces.append(escape(text[m.start():m.end()]))
        pieces.append(Markup("</mark>"))
        last = m.end()
    pieces.append(escape(text[last:]))
    return Markup("").join(pieces)


def card_excerpt(text: str, query: str, max_len: int = 220, radius: int = 90) -> dict:
    """
    اگر متن از max_len بلندتر باشد:
      - اگر عبارتِ جستجو در همان max_len کاراکترِ ابتدایی باشد، مثلِ قبل
        از ابتدا بریده می‌شود.
      - اگر بیرون از آن باشد، یک بازه دور و برِ محلِ match (radius کاراکتر
        از هر طرف) برگردانده می‌شود تا هایلایت در پیش‌نمایشِ کارت هم دیده
        شود؛ ابتدا/انتهای بازه با «…» نشان داده می‌شود.
    خروجی: {"text": <رشتهٔ نمایشی>, "truncated": <bool — آیا دکمهٔ
    «نمایش بیشتر» لازم است>}
    """
    text = text or ""
    if len(text) <= max_len:
        return {"text": text, "truncated": False}

    pattern = _compile_query(query)
    m = pattern.search(text) if pattern else None

    if m is None or m.start() < max_len:
        # match در همان تکهٔ ابتداییِ قبلی هست (یا اصلاً match نداریم)؛
        # رفتارِ قبلی حفظ می‌شود.
        return {"text": text[:max_len], "truncated": True}

    start = max(0, m.start() - radius)
    end = min(len(text), m.end() + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return {"text": f"{prefix}{text[start:end]}{suffix}", "truncated": True}
