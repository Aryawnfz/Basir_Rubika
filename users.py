"""
انباره کاربرانِ ورود به سامانه — JSON-backed CRUD.
هر کاربر: { username, password_hash, role }

نقش‌ها:
  - "admin": دسترسی کامل (مدیریت کاربران و اکانت‌های روبیکا).
  - "user":  فقط جستجو، دیدنِ نتایج و گزارش‌ها.

کاربرِ اصلیِ مدیر (config.ADMIN_USER) همیشه با نقش admin وجود دارد و
قابل حذف نیست.
"""
import json
import os

from werkzeug.security import generate_password_hash, check_password_hash

import config

USERS_FILE = os.path.join(config.DATA_DIR, "users.json")

ROLE_ADMIN = "admin"
ROLE_USER = "user"


def _ensure_dir():
    os.makedirs(config.DATA_DIR, exist_ok=True)


def _read() -> list[dict]:
    if not os.path.exists(USERS_FILE):
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write(users: list[dict]) -> None:
    _ensure_dir()
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USERS_FILE)


def _seed_admin(users: list[dict]) -> list[dict]:
    """تضمین می‌کند کاربرِ اصلیِ مدیر (از config) همیشه وجود دارد."""
    if not any(u.get("username") == config.ADMIN_USER for u in users):
        users.insert(0, {
            "username": config.ADMIN_USER,
            "password_hash": generate_password_hash(config.ADMIN_PASS),
            "role": ROLE_ADMIN,
        })
        _write(users)
    return users


def load_users() -> list[dict]:
    return _seed_admin(_read())


def get_user(username: str) -> dict | None:
    for u in load_users():
        if u.get("username") == username:
            return u
    return None


def is_admin_username(username: str) -> bool:
    """کاربرِ اصلیِ مدیر (config.ADMIN_USER) — غیرقابل حذف/تغییرِ نقش."""
    return username == config.ADMIN_USER


def verify(username: str, password: str) -> dict | None:
    """اعتبارسنجیِ ورود؛ در صورت موفقیت رکوردِ کاربر را برمی‌گرداند."""
    u = get_user(username)
    if u and check_password_hash(u.get("password_hash", ""), password):
        return u
    return None


def add_user(username: str, password: str, role: str = ROLE_USER) -> tuple[bool, str]:
    username = (username or "").strip()
    if not username or not password:
        return False, "نام کاربری و رمز عبور الزامی است."
    if role not in (ROLE_ADMIN, ROLE_USER):
        role = ROLE_USER
    users = load_users()
    if any(u.get("username") == username for u in users):
        return False, "کاربری با این نام از قبل وجود دارد."
    users.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
    })
    _write(users)
    return True, "کاربر اضافه شد."


def delete_user(username: str) -> tuple[bool, str]:
    if is_admin_username(username):
        return False, "کاربرِ مدیرِ اصلی قابل حذف نیست."
    users = load_users()
    new_list = [u for u in users if u.get("username") != username]
    if len(new_list) == len(users):
        return False, "کاربر یافت نشد."
    _write(new_list)
    return True, "کاربر حذف شد."


def set_password(username: str, password: str) -> tuple[bool, str]:
    if not password:
        return False, "رمز عبور الزامی است."
    users = load_users()
    for u in users:
        if u.get("username") == username:
            u["password_hash"] = generate_password_hash(password)
            _write(users)
            return True, "رمز عبور تغییر کرد."
    return False, "کاربر یافت نشد."
