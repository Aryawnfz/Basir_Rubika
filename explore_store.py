"""
انباره کانال‌های «کاوش» — JSON-backed CRUD.

کانال‌های «کاوش» سراسری‌اند: هر کاربری که کانالی اضافه کند برای همهٔ کاربران
نمایش داده می‌شود تا وضعیتِ کاوش کاملاً یکپارچه و اشتراکی باشد.

هر رکورد کانال:
{ id, username, url, title, bio, members, members_count,
  avatar_url, avatar_letter, avatar_color, added_by, created_at }
"""
import json
import os
import time
import uuid

import config

EXPLORE_FILE = os.path.join(config.DATA_DIR, "explore_channels.json")


def _ensure_dir() -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)


def _read() -> list[dict]:
    if not os.path.exists(EXPLORE_FILE):
        return []
    try:
        with open(EXPLORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write(channels: list[dict]) -> None:
    _ensure_dir()
    tmp = EXPLORE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EXPLORE_FILE)


def list_channels() -> list[dict]:
    """همهٔ کانال‌های افزوده‌شده (سراسری، جدیدترین اول)."""
    items = _read()
    return sorted(items, key=lambda c: c.get("created_at", 0), reverse=True)


def get_channel(channel_id: str) -> dict | None:
    for c in _read():
        if c.get("id") == channel_id:
            return c
    return None


def add_channel(owner: str, info: dict) -> tuple[bool, str, dict | None]:
    """
    افزودنِ یک کانال (سراسری). اگر کانالی با همان username از قبل باشد،
    دوباره اضافه نمی‌شود.
    """
    username = (info.get("username") or "").strip()
    if not username:
        return False, "کانال پیدا نشد.", None
    channels = _read()
    for c in channels:
        if c.get("username", "").lower() == username.lower():
            return False, "این کانال قبلاً اضافه شده است.", c
    record = {
        "id":            str(uuid.uuid4()),
        "username":      username,
        "url":           info.get("url", ""),
        "title":         info.get("title", "") or username,
        "bio":           info.get("bio", ""),
        "members":       info.get("members", ""),
        "members_count": info.get("members_count", 0),
        "avatar_url":    info.get("avatar_url", ""),
        "avatar_letter": info.get("avatar_letter", ""),
        "avatar_color":  info.get("avatar_color", ""),
        "added_by":      owner,
        "created_at":    time.time(),
    }
    channels.append(record)
    _write(channels)
    return True, "کانال اضافه شد.", record


def delete_channel(channel_id: str) -> bool:
    channels = _read()
    new_list = [c for c in channels if c.get("id") != channel_id]
    if len(new_list) == len(channels):
        return False
    _write(new_list)
    return True


def delete_all_channels() -> int:
    """حذفِ همهٔ کانال‌ها (سراسری). تعدادِ حذف‌شده را برمی‌گرداند."""
    channels = _read()
    _write([])
    return len(channels)


def update_channel(channel_id: str, info: dict) -> dict | None:
    """
    به‌روزرسانیِ اطلاعاتِ نمایشیِ یک کانال (عنوان/بیو/اعضا/آواتار) با
    داده‌ی تازه‌خوانده‌شده از وب‌اپِ روبیکا — درست مثل اینکه همین الان
    دوباره اضافه شده باشد. id، added_by و created_at دست‌نخورده می‌مانند.
    """
    channels = _read()
    updated = None
    for c in channels:
        if c.get("id") == channel_id:
            c["title"]         = info.get("title") or c.get("title", "")
            c["bio"]           = info.get("bio", "")
            c["members"]       = info.get("members", "")
            c["members_count"] = info.get("members_count", 0)
            c["avatar_url"]    = info.get("avatar_url", "")
            c["avatar_letter"] = info.get("avatar_letter", "")
            c["avatar_color"]  = info.get("avatar_color", "")
            if info.get("url"):
                c["url"] = info["url"]
            updated = c
            break
    if updated is None:
        return None
    _write(channels)
    return updated
