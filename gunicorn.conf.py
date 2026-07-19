"""
تنظیمات Gunicorn برای بصیر

مهم‌ترین نکته:
  workers = 1  ← چون Playwright session در background thread اجرا میشه
                 بیشتر از یه worker باعث میشه درخواست‌ها به process های
                 مختلف برن و session از دست بره.

  threads = 4  ← چند thread برای هندل کردن همزمان چند request در یه process
"""
import multiprocessing

# ── Workers ────────────────────────────────────────────────────────────────────
# حتماً 1 نگه دار — Playwright session در in-process background thread هست
workers = 1
threads = 4
worker_class = "gthread"

# ── Binding ────────────────────────────────────────────────────────────────────
bind = "127.0.0.1:5000"

# ── Timeouts ───────────────────────────────────────────────────────────────────
# جستجوی Playwright ممکنه طولانی باشه
timeout         = 300   # 5 دقیقه
graceful_timeout = 60
keepalive       = 5

# ── Logging ────────────────────────────────────────────────────────────────────
accesslog = "-"
errorlog  = "-"
loglevel  = "info"

# ── Process ────────────────────────────────────────────────────────────────────
preload_app = True   # یه بار app رو لود کن، بعد fork
daemon      = False  # systemd خودش daemonize می‌کنه
