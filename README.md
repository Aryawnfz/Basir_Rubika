# ◈ بصیر – Basir (روبیکا)

پلتفرم هوشمند جستجوی چنداکانته در روبیکا

> این نسخه دقیقاً بر اساس «بصیر بله» پیاده‌سازی شده است — با همان امکانات
> (لاگین OTP چنداکانته، صفِ jobهای هم‌زمان، گزارش فعالیت‌ها، خروجی اکسل و
> آمار کلی، ری‌اکشن‌ها و بازدید). تفاوت با بله: روبیکا فقط «جستجوی عادی»
> دارد (بدون جستجوی کانال‌به‌کانالِ ایتا و بدون جستجوی سراسری).

---

## نصب سریع

```bash
# ۱. وابستگی‌ها
pip install -r requirements.txt
playwright install chromium

# ۲. اجرا (development)
python app.py

# ۳. اجرا (production با Gunicorn)
gunicorn -c gunicorn.conf.py app:app
```

> نکته: `rubika_search.py` مرورگر را به‌صورت headed (headless=False) اجرا می‌کند،
> پس روی سرور بدون نمایشگر به یک X مجازی نیاز است، مثلاً:
> `xvfb-run -a gunicorn -c gunicorn.conf.py app:app` یا `DISPLAY=:0`.

---

## Deploy روی سرور لینوکسی

```bash
# ۱. کپی پروژه
sudo mkdir /opt/basir
sudo cp -r . /opt/basir/

# ۲. venv
cd /opt/basir
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium

# ۳. تنظیم مسیر در basir.service
sudo nano basir.service     # WorkingDirectory و ExecStart رو عوض کن

# ۴. نصب سرویس
sudo cp basir.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable basir
sudo systemctl start basir

# ۵. Nginx
sudo cp nginx.conf /etc/nginx/sites-available/basir
sudo ln -s /etc/nginx/sites-available/basir /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## ⚠️ نکته مهم Gunicorn

حتماً از `gunicorn.conf.py` استفاده کن که `workers = 1` داره.
اگه workers > 1 بذاری، login flow کار نمی‌کنه چون session در RAM یه worker هست.

---

## ورود پیش‌فرض

| نام کاربری | رمز |
|---|---|
| `admin` | `basir1234` |

رمز رو از طریق متغیر محیطی `BASIR_PASS` یا در `config.py` عوض کن.

---

## نکتهٔ فنی دربارهٔ سلکتورهای روبیکا

وب روبیکا (`web.rubika.ir`) یک کلاینت مبتنی بر **Telegram Web K** است. سلکتورهای
جستجو/ری‌اکشن/لاگین در `rubika_search.py` و `login_manager.py` بر همین اساس و با
چند لایه fallback نوشته شده‌اند. اگر پس از اولین ورودِ واقعی، عناصر رابط کاربری
روبیکا تغییر کرده بودند، فقط کافی است لیست‌های سلکتور در ابتدای این دو فایل
به‌روزرسانی شوند.
