# پینگ‌ایکس بات (Aiogram + 3x-ui + کیف پول)
ربات تلگرام برای فروش و مدیریت اشتراک 3x-ui با کیف پول داخلی، SQLite و پنل ادمین/پشتیبان.

## معرفی
پینگ‌ایکس کاربران را احراز عضویت کانال، کیف پول را شارژ و پلن را از طریق 3x-ui ایجاد/تمدید می‌کند. لینک سابسکریپشن (به‌همراه QR)، هشدار مصرف/انقضا، تیکت پشتیبانی و گزارش‌های فروش در بات ارائه می‌شود.

## امکانات
- خرید پلن با کیف پول و تحویل QR/لینک سابسکریپشن
- تمدید/آپگرید روی همان کلاینت فعال (تمدید expiry + افزودن ترافیک، لینک ثابت)
- تخفیف سراسری قابل تنظیم از پنل ادمین
- مرتب‌سازی پلن‌ها (sort_order) و جابه‌جایی بالا/پایین
- همگام‌سازی دوره‌ای مصرف از 3x-ui + هشدار نزدیک مصرف/انقضا
- تیکت پشتیبانی با ارسال به گروه و پاسخ دوطرفه
- نقش پشتیبان (Support) با دسترسی محدود (تیکت + تأیید/رد پرداخت)
- مدیریت رسید شارژ در گروه پشتیبانی با دکمه تایید/رد
- گزارش فروش/نرخ تبدیل (امروز، ۷ و ۳۰ روز)
- تنظیمات درون‌باتی: کانال اجباری، کارت بانکی، تخفیف، پشتیبان‌ها، قالب پیام و ...

## نقش‌ها و دسترسی‌ها
- **ادمین**: همه چیز (پلن‌ها، تنظیمات، گزارش‌ها، کاربران، تیکت‌ها، پرداخت‌ها، تخفیف، مرتب‌سازی پلن).
- **پشتیبان**: فقط تیکت‌ها + تایید/رد پرداخت‌ها (بدون دسترسی به تنظیمات/پلن‌ها/گزارش‌ها).
- **کاربر**: خرید، مشاهده اشتراک، آمار مصرف، تیکت، شارژ کیف پول.
جزئیات بیشتر: [docs/ROLES_AND_PERMISSIONS.md](docs/ROLES_AND_PERMISSIONS.md)

## نیازمندی‌ها
- Python 3.10+
- Git
- SQLite (پیش‌فرض درون برنامه)
- دسترسی به پنل 3x-ui (URL، کاربر، رمز و inbound فعال)
- BotFather برای دریافت توکن ربات

## نصب سریع (بدون داکر)
```bash
git clone https://github.com/AliJaB3/pingx.git
cd pingx
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
```

## اجرای محلی
1) فایل `.env` را بسازید:
```env
TELEGRAM_BOT_TOKEN=123456:BOT-TOKEN-HERE
ADMIN_IDS=11111111,22222222
DB_PATH=bot.db

THREEXUI_BASE_URL=https://panel.example.com
THREEXUI_USERNAME=admin
THREEXUI_PASSWORD=your-password
THREEXUI_INBOUND_ID=39

REQUIRED_CHANNEL=@yourchannel
SUB_HOST=
SUB_SCHEME=https
SUB_PORT=2096
SUB_PATH=/sub/

CARD_NUMBER=0000-0000-0000-0000
MAX_RECEIPT_MB=5
MAX_RECEIPT_PHOTOS=3

PAGE_SIZE_USERS=10
PAGE_SIZE_PAYMENTS=10
PAGE_SIZE_TICKETS=10
TICKET_GROUP_ID=0
SUPPORT_GROUP_ID=0
```
2) اجرا:
```bash
python main.py
```
بخش زیادی از تنظیمات در جدول settings ذخیره می‌شود و از طریق پنل ادمین قابل ویرایش است.

## تنظیمات کلیدی
شرح کامل و مقادیر پیشنهادی در [docs/CONFIGURATION.md](docs/CONFIGURATION.md).  
کلیدهای مهم: اتصال 3x-ui، ACTIVE_INBOUND_ID، کانال اجباری، کارت و محدودیت رسید، تخفیف سراسری، SUPPORT_IDS، گروه‌های تیکت/پشتیبانی، مقادیر SUB_* برای لینک سابسکریپشن.

## راه‌اندازی روی سرور (systemd)
1) پروژه و `.env` را روی سرور قرار دهید.  
2) فایل `deploy/pingx-bot.service` را با مسیر venv، مسیر پروژه و Environment فایل ویرایش کنید.  
3) سرویس را فعال کنید:
```bash
sudo cp deploy/pingx-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pingx-bot
sudo systemctl status pingx-bot
```

## ساختار پروژه (خلاصه)
```
handlers/     # روترهای کاربر، ادمین، پرداخت، تیکت
db.py         # اتصال و توابع SQLite + migration
keyboards.py  # کیبوردهای تلگرام
xui.py        # ارتباط با 3x-ui
scheduler.py  # کران همگام‌سازی مصرف و هشدارها
deploy/       # نمونه سرویس systemd
docs/         # مستندات عملیاتی
```

## عیب‌یابی سریع
- **عدم ورود به 3x-ui**: URL/کاربر/رمز و حذف /panel از انتهای آدرس را بررسی کنید؛ خطای HTML یعنی ورود ناموفق است.
- **گروه رسید/تیکت کار نمی‌کند**: `TICKET_GROUP_ID` یا `SUPPORT_GROUP_ID` را عددی تنظیم و ربات را با مجوز ارسال پیام اضافه کنید.
- **هشدار مصرف/انقضا نمی‌آید**: سرویس scheduler فعال است؟ اتصال به 3x-ui و مقادیر total/expiry روی کلاینت را بررسی کنید.
- **لینک اشتراک نادرست**: SUB_HOST/SUB_SCHEME/SUB_PORT/SUB_PATH را اصلاح و از گزینه «لینک جدید» در بات استفاده کنید.
- **تخفیف اعمال نمی‌شود**: مقدار GLOBAL_DISCOUNT_PERCENT را در تنظیمات ادمین بررسی کنید (۰ تا ۹۰).

## امنیت و توصیه‌ها
- توکن ربات و رمز پنل را فقط در `.env` یا Environment سرویس ذخیره کنید؛ آن‌ها را در مخزن قرار ندهید.
- ادمین/پشتیبان را فقط به افراد مطمئن بدهید؛ پشتیبان به تنظیمات/گزارش دسترسی ندارد.
- برای 3x-ui از HTTPS و دسترسی محدود استفاده کنید؛ در صورت استفاده از Cloudflare به time-out‌ها توجه کنید.

## مستندات تکمیلی
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/ROLES_AND_PERMISSIONS.md](docs/ROLES_AND_PERMISSIONS.md)
- [docs/PAYMENTS_AND_WALLET.md](docs/PAYMENTS_AND_WALLET.md)
- [docs/SUBSCRIPTIONS_RENEW_UPGRADE.md](docs/SUBSCRIPTIONS_RENEW_UPGRADE.md)
- [docs/SCHEDULER_USAGE_AUTOMATION.md](docs/SCHEDULER_USAGE_AUTOMATION.md)
- [docs/REPORTS.md](docs/REPORTS.md)
- [docs/ADMIN_FEATURES.md](docs/ADMIN_FEATURES.md)

## مجوز
MIT
