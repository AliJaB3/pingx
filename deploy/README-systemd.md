راهنمای سرویس systemd برای PingX Bot

این راهنما نحوه اجرای ربات PingX به‌عنوان سرویس systemd را به‌صورت کامل توضیح می‌دهد تا بعد از ریبوت سرور به‌طور خودکار اجرا شود و لاگ‌ها از طریق journalctl قابل مشاهده باشند.

پرونده‌های مربوطه
- `deploy/pingx-bot.service`: نمونه فایل واحد سرویس systemd که باید در سرور کپی و ویرایش شود.

فرضیات پیش‌فرض داخل فایل سرویس
- مسیر پروژه: `/opt/pingx-bot`
- محیط مجازی: `/opt/pingx-bot/.venv`
- فایل env نقطه‌ای: `/opt/pingx-bot/.env`
- کاربر/گروه اجرای سرویس: `pingx:pingx`

در صورت تفاوت ساختار سرور، این مسیرها را مطابق نیاز خود تغییر دهید.

پیش‌نیازها
- سیستم‌عامل: Ubuntu 20.04+/Debian 11+/AlmaLinux/RHEL 8+
- Python 3.10 به بالا، pip و venv
- git برای دریافت کد
- دسترسی sudo برای نصب سرویس

گام 1: ساخت کاربر و پوشه پروژه
- ایجاد کاربر سیستمی (اختیاری ولی توصیه‌شده):
  sudo useradd --system --create-home --shell /usr/sbin/nologin pingx
- ساخت مسیر پروژه و اعمال مالکیت:
  sudo mkdir -p /opt/pingx-bot
  sudo chown -R pingx:pingx /opt/pingx-bot

گام 2: دریافت کد پروژه در سرور
- با git:
  sudo -u pingx git clone https://github.com/AliJaB3/pingx.git /opt/pingx-bot
- یا اگر قبلاً کد را جای دیگری دارید، با rsync/scp به `/opt/pingx-bot` منتقل کنید و مالکیت را تنظیم کنید:
  sudo chown -R pingx:pingx /opt/pingx-bot

گام 3: ساخت محیط مجازی و نصب وابستگی‌ها
- ایجاد venv و نصب پکیج‌ها:
  sudo -u pingx python3 -m venv /opt/pingx-bot/.venv
  sudo -u pingx /opt/pingx-bot/.venv/bin/pip install --upgrade pip
  sudo -u pingx /opt/pingx-bot/.venv/bin/pip install -r /opt/pingx-bot/requirements.txt

گام 4: تنظیم فایل `.env`
- در مسیر پروژه یک فایل `.env` بسازید و تنظیمات را وارد کنید:
  sudo -u pingx nano /opt/pingx-bot/.env
- مقادیر نمونه (بر اساس `config.py`):
  TELEGRAM_BOT_TOKEN=123456:ABCDEF-your-bot-token
  ADMIN_IDS=11111111,22222222
  DB_PATH=bot.db
  REQUIRED_CHANNEL=@piingx
  THREEXUI_BASE_URL=https://panel.example.com
  THREEXUI_USERNAME=admin
  THREEXUI_PASSWORD=strong-password
  THREEXUI_INBOUND_ID=39
  SUB_HOST=
  SUB_SCHEME=https
  SUB_PORT=2096
  SUB_PATH=/sub/
  CARD_NUMBER=6037-XXXX-XXXX-XXXX
  MAX_RECEIPT_MB=5
  MAX_RECEIPT_PHOTOS=3
  PAGE_SIZE_USERS=10
  PAGE_SIZE_PAYMENTS=10
  PAGE_SIZE_TICKETS=10

نکته‌ها در مورد تنظیمات
- `TELEGRAM_BOT_TOKEN` الزامی است؛ نبود آن باعث توقف برنامه می‌شود.
- `ADMIN_IDS` لیست آی‌دی عددی ادمین‌ها به‌صورت جداشده با کاما است.
- `THREEXUI_*` برای اتصال به پنل 3x-ui استفاده می‌شود. اگر استفاده نمی‌کنید، می‌توانید خالی بگذارید.
- `SUB_*` برای ساخت لینک اشتراک با الگوی `<scheme>://<host>:<port><path><subId>` است.
- فایل `DB_PATH` در همان پوشه پروژه ساخته می‌شود؛ کاربر سرویس باید مجوز نوشتن داشته باشد.

گام 5: تست اجرای دستی (اختیاری اما مفید)
- اجرای ربات در فورگراند جهت اطمینان از عدم خطا:
  cd /opt/pingx-bot
  sudo -u pingx /opt/pingx-bot/.venv/bin/python main.py
- با Ctrl+C خارج شوید. در صورت مشاهده خطایی مثل «Login to 3x-ui failed»، تنظیمات `.env` را بازبینی کنید.

گام 6: نصب سرویس systemd
- کپی فایل سرویس و ویرایش مسیرها در صورت نیاز:
  sudo cp /opt/pingx-bot/deploy/pingx-bot.service /etc/systemd/system/pingx-bot.service
  sudo nano /etc/systemd/system/pingx-bot.service
- موارد مهم داخل سرویس:
  - `User` و `Group` باید با کاربری که مالک پروژه است هماهنگ باشد (پیشنهاد: `pingx`).
  - `WorkingDirectory` باید مسیر ریشه پروژه باشد: `/opt/pingx-bot`.
  - `EnvironmentFile` باید به `.env` اشاره کند: `/opt/pingx-bot/.env`.
  - `ExecStart` باید به باینری پایتون داخل venv اشاره کند: `/opt/pingx-bot/.venv/bin/python main.py`.

گام 7: فعال‌سازی و اجرا
- بارگذاری مجدد واحدها و فعال‌سازی سرویس:
  sudo systemctl daemon-reload
  sudo systemctl enable --now pingx-bot
- مشاهده وضعیت و لاگ زنده:
  sudo systemctl status pingx-bot
  sudo journalctl -u pingx-bot -f

به‌روزرسانی کد و وابستگی‌ها
- کشیدن آخرین تغییرات و ریستارت سرویس:
  cd /opt/pingx-bot
  sudo -u pingx git pull --ff-only
  sudo -u pingx /opt/pingx-bot/.venv/bin/pip install -r requirements.txt
  sudo systemctl restart pingx-bot

نکات امنیتی و سخت‌سازی
- `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=full`, `ProtectHome=true` در سرویس فعال شده‌اند. در صورت نیاز به نوشتن خارج از مسیر پروژه یا SELinux، ممکن است لازم باشد آن‌ها را موقتاً غیرفعال یا تنظیم کنید.
- مطمئن شوید فقط کاربر سرویس به `.env` و دیتابیس دسترسی دارد: `chmod 600 /opt/pingx-bot/.env` و مالکیت `pingx:pingx`.

عیب‌یابی متداول
- برنامه بلافاصله می‌ایستد: مقدار `TELEGRAM_BOT_TOKEN` در `.env` تنظیم نشده است.
- خطای 3x-ui: آدرس/نام کاربری/رمز عبور یا `THREEXUI_INBOUND_ID` نادرست است؛ یا پنل در دسترس نیست.
- `permission denied` روی دیتابیس: مالکیت/مجوزهای مسیر `/opt/pingx-bot` را برای کاربر سرویس درست کنید.
- مسیر `ExecStart` اشتباه: نسخه پایتون یا مسیر venv چک شود.
- سرویس اجرا نمی‌شود پس از ویرایش سرویس: `sudo systemctl daemon-reload` را فراموش نکنید.

حذف سرویس
- توقف و غیرفعال‌سازی:
  sudo systemctl stop pingx-bot
  sudo systemctl disable pingx-bot
- حذف فایل سرویس و بارگذاری مجدد:
  sudo rm -f /etc/systemd/system/pingx-bot.service
  sudo systemctl daemon-reload

یادداشت‌ها
- تمام متغیرهای محیطی از فایل `/.env` خوانده می‌شوند؛ رمزها را داخل آن نگه دارید و هرگز داخل git کامیت نکنید.
- اگر از توزیع‌های غیر Debian/Ubuntu استفاده می‌کنید، مسیرهای پایتون ممکن است متفاوت باشند.

