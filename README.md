# PingX Bot (3x-ui)

بات تلگرامی برای فروش و مدیریت اشتراک VPN روی 3x-ui/3x-UI با Python و Aiogram.

## چه کار می‌کند؟
- لاگین به 3x-ui و ساخت/ویرایش کلاینت‌ها (subId، محدودیت حجم/زمان/دستگاه)
- فروش پلن‌ها از داخل تلگرام با پایگاه‌داده SQLite
- تحویل لینک سابسکریپشن و QR کد خودکار
- ربات ادمین: مدیریت کاربران، پرداخت‌ها، تیکت‌ها
- رفرال پیشرفته: لینک اختصاصی با توضیح، نرخ تبدیل، آخرین عضوهای جذب‌شده
- اجبار عضویت کانال قبل از خرید (Force-Join)

## پیش‌نیازها
- Python 3.10 یا جدیدتر
- Git
- توکن بات تلگرام (BotFather)
- دسترسی به یک پنل 3x-ui سالم

## راه‌اندازی سریع
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

## تنظیمات (.env نمونه)
```
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
```
توضیح متغیرها:
- `TELEGRAM_BOT_TOKEN`: توکن بات.
- `ADMIN_IDS`: شناسه تلگرام ادمین‌ها (کامای جداکننده).
- `THREEXUI_*`: اطلاعات ورود و اینباند هدف در 3x-ui.
- `SUB_*`: اگر هاست/پورت متفاوت از پنل دارید برای ساخت لینک ساب.
- `CARD_NUMBER`, `MAX_RECEIPT_*`: برای پرداخت‌های دستی و محدودیت آپلود.
- `REQUIRED_CHANNEL`: کانال اجباری برای ورود/خرید.

## اجرا
```bash
python main.py
```
بات پس از بالا آمدن، جدول‌های SQLite را خودکار می‌سازد (WAL فعال است).

## امکانات رفرال (نسخه ارتقا‌یافته)
- ساخت لینک رفرال: منوی ادمین ➜ «لینک‌های رفرال» ➜ «ساخت لینک جدید»
- توضیح دلخواه برای هر لینک (ویرایش عنوان/توضیح داخل پنل)
- آمار: کلیک، ثبت‌نام، نرخ تبدیل، تاریخ ساخت
- جزئیات: نمایش آخرین عضوهای جذب‌شده (آیدی/یوزرنیم/نام و زمان پیوستن)
- لینک‌ها قابل کلیک هستند و صفحهٔ جزئیات ویرایش‌پذیر دارند.

## نکات 3x-ui
- `THREEXUI_BASE_URL` باید آدرس قابل دسترس بات باشد (HTTPS توصیه می‌شود).
- `THREEXUI_INBOUND_ID` را روی اینباندی بگذارید که می‌خواهید کلاینت‌ها داخل آن ساخته شوند.
- اگر از Cloudflare/پروکسی استفاده می‌کنید، زمان‌بندی Timeout را پایین نگذارید.

## نکات عملیاتی
- سرویس طولانی‌مدت: از systemd / pm2 / Windows Service استفاده کنید.
- نسخه پایتون و `requirements.txt` را ثابت نگه دارید تا از ناسازگاری جلوگیری شود.
- از فایل `.env` بک‌آپ بگیرید و در مخزن عمومی قرار ندهید.

## رفع اشکال سریع
- خطای لاگین 3x-ui: آدرس/یوزر/پسورد یا کپچا/Rate-limit را چک کنید.
- اجبار عضویت: مطمئن شوید بات در کانال ادمین است و `REQUIRED_CHANNEL` درست نوشته شده.
- خطای 404 بعد از مدتی: نسخه فعلی، سشن‌های منقضی شده را تشخیص می‌دهد و دوباره لاگین می‌کند؛ لاگ‌ها را برای هشدار «session expired» بررسی کنید.

## لایسنس
MIT
