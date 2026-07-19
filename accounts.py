"""
Rubika account store – JSON-backed CRUD.
هر اکانت: { id, name, phone, user_data_dir, logged_in }
"""
import json
import os
import shutil
import uuid

from config import ACCOUNTS_FILE, PROFILES_DIR, DATA_DIR


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROFILES_DIR, exist_ok=True)


def load_accounts() -> list[dict]:
    _ensure_dirs()
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []

    # backward-compat: اکانت‌های قدیمی که user_data_dir ندارن
    changed = False
    for a in data:
        if "user_data_dir" not in a:
            a["user_data_dir"] = os.path.join(PROFILES_DIR, a["id"])
            a.pop("session_file", None)
            changed = True
    if changed:
        _save_accounts(data)
    return data


def _save_accounts(accounts: list[dict]):
    _ensure_dirs()
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def add_account(name: str, phone: str) -> dict:
    accounts = load_accounts()
    account_id = str(uuid.uuid4())
    account = {
        "id":            account_id,
        "name":          name.strip(),
        "phone":         phone.strip(),
        "user_data_dir": os.path.join(PROFILES_DIR, account_id),
        "logged_in":     False,
    }
    accounts.append(account)
    _save_accounts(accounts)
    return account


def delete_account(account_id: str) -> bool:
    accounts = load_accounts()
    to_delete = get_account(account_id)
    new_list = [a for a in accounts if a["id"] != account_id]
    if len(new_list) == len(accounts):
        return False
    if to_delete:
        profile_dir = to_delete.get("user_data_dir", "")
        if profile_dir and os.path.isdir(profile_dir):
            try:
                shutil.rmtree(profile_dir)
            except OSError:
                pass
    _save_accounts(new_list)
    return True


def get_account(account_id: str) -> dict | None:
    for a in load_accounts():
        if a["id"] == account_id:
            return a
    return None


def mark_logged_in(account_id: str):
    accounts = load_accounts()
    for a in accounts:
        if a["id"] == account_id:
            a["logged_in"] = True
    _save_accounts(accounts)
