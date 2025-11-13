PingX Bot — ربات تلگرام مدیریت اشتراک (3x‑ui)

این پروژه یک ربات تلگرام بر پایه Python/aiogram است که فرآیند فروش و مدیریت اشتراک سرورهای پروکسی/VPN متصل به پنل 3x‑ui را خودکار می‌کند: شارژ کیف‌پول، خرید پلن، ساخت کاربر در 3x‑ui، تولید لینک/QR سابسکرایب، مشاهده مصرف، تیکت پشتیبانی، و منوی ادمین.

ویژگی‌ها
- اتصال مستقیم به 3x‑ui (لاگین، افزودن/به‌روزرسانی کلاینت، دریافت مصرف، چرخاندن subId)
- کیف‌پول کاربر + خرید پلن‌ها از موجودی
- ثبت شارژ با رسید تصویری و تأیید/رد توسط ادمین‌ها
- نمایش لینک سابسکرایب + QR و رفرش مصرف از پنل
- تیکت پشتیبانی کاربر و پاسخ ادمین (FSM)
- نوتیف ساعت‌به‌ساعت: نزدیک شدن به انقضا و عبور از ۸۰٪ مصرف

پیش‌نیازها
- Python 3.10 یا جدیدتر
- Git (برای کلون/دیپلوی)
- دسترسی ساخت Bot در تلگرام (BotFather)
- دسترسی به پنل 3x‑ui در سرور شما (آدرس و حساب کاربری)

نصب و اجرا (لوکال)
1) کلون پروژه
   - `git clone https://github.com/AliJaB3/pingx.git`
   - `cd pingx`
2) ایجاد و فعال‌سازی محیط مجازی (اختیاری ولی توصیه‌شده)
   - Windows: `python -m venv .venv && .\.venv\Scripts\activate`
   - Linux/macOS: `python3 -m venv .venv && source .venv/bin/activate`
3) نصب وابستگی‌ها
   - `pip install -r requirements.txt`
4) تنظیم متغیرهای محیطی (.env)
   - یک فایل `.env` در ریشه پروژه بسازید (نمونه در ادامه)
5) اجرای ربات
   - `python main.py`

تنظیمات (Environment Variables)
- `TELEGRAM_BOT_TOKEN` (اجباری): توکن ربات از BotFather.
- `ADMIN_IDS`: شناسه عددی ادمین‌ها، جدا با کاما. مثال: `12345,67890`
- `DB_PATH`: مسیر دیتابیس SQLite. پیش‌فرض `bot.db`.
- `REQUIRED_CHANNEL`: کانال تلگرامی لازم برای عضویت (برای Force-Join). مثال: `@mychannel`
- اتصال 3x‑ui:
  - `THREEXUI_BASE_URL`: مثل `https://panel.example.com`
  - `THREEXUI_USERNAME`, `THREEXUI_PASSWORD`: حساب ورود پنل
  - `THREEXUI_INBOUND_ID`: آیدی اینباند مقصد برای ایجاد کلاینت‌ها
- لینک سابسکرایب (برای ساخت URL مشترک):
  - `SUB_HOST`: اگر خالی باشد از هاست `THREEXUI_BASE_URL` استفاده می‌شود
  - `SUB_SCHEME`: پیش‌فرض `https`
  - `SUB_PORT`: پیش‌فرض `2096`
  - `SUB_PATH`: پیش‌فرض `/sub/`
- پرداخت/رسید:
  - `CARD_NUMBER`: شماره کارت نمایش‌داده‌شده به کاربر
  - `MAX_RECEIPT_MB`: حداکثر حجم هر عکس رسید (مگابایت)
  - `MAX_RECEIPT_PHOTOS`: حداکثر تعداد عکس رسید
- صفحه‌بندی پنل ادمین:
  - `PAGE_SIZE_USERS`, `PAGE_SIZE_PAYMENTS`, `PAGE_SIZE_TICKETS`

نمونه فایل .env
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

نحوه استفاده سریع
- در تلگرام `/start` را ارسال کنید.
- اگر `REQUIRED_CHANNEL` ست شده، ابتدا ربات عضویت شما را در کانال بررسی می‌کند.
- از منوی اصلی:
  - «کیف‌پول» → مشاهده موجودی و «شارژ کیف‌پول» با ارسال رسید.
  - «خرید اشتراک» → انتخاب پلن و پرداخت از کیف‌پول.
  - «اشتراک‌های من» → مشاهده جزئیات، لینک سابسکرایب/QR، چرخش subId، و به‌روزرسانی مصرف.
  - «پشتیبانی» → ایجاد/باز کردن تیکت و گفتگو با ادمین.
- بخش ادمین برای `ADMIN_IDS` فعال می‌شود: لیست/جستجوی کاربر، واریز/برداشت کیف‌پول، مشاهده خریدها و مصرف، تأیید/رد پرداخت‌ها و…

نکات 3x‑ui
- `THREEXUI_BASE_URL` باید به URL پنل با دسترسی کامل اشاره کند. ربات تلاش می‌کند با چند مسیر متداول API لاگین و کار کند.
- `THREEXUI_INBOUND_ID` اینباندی است که کلاینت‌های جدید روی آن اضافه می‌شوند.
- اگر SUB_HOST ست نشود، هاست از آدرس پنل استخراج می‌شود. خروجی لینک شبیه این است:
  - `https://<host>:<port>/sub/<subId>`

دیتابیس
- SQLite با WAL (`PRAGMA journal_mode=WAL`) و `synchronous=NORMAL`.
- جدول‌ها در اولین اجرا ساخته/مهاجرت می‌شوند (تابع `migrate`).

دیپلوی
- می‌توانید این بات را روی سروری که به پنل 3x‑ui دسترسی شبکه‌ای دارد اجرا کنید.
- برای اجرای پایدار، یک سرویس systemd یا PM2/Windows Service تنظیم کنید.
- پیشنهاد: فایل `.env` را کنار برنامه نگه دارید و در گیت کامیت نکنید.

عیب‌یابی
- خطای «Login to 3x‑ui failed»: آدرس/نام‌کاربری/پسورد یا دسترسی شبکه را بررسی کنید.
- «Required channel»: مطمئن شوید ربات در کانال ادمین است و نام کانال درست است.
- اگر متن‌های فارسی به‌هم‌ریخته نمایش داده شد، اطمینان حاصل کنید فایل‌ها با UTF‑8 ذخیره شده باشند.

مجوز
- این پروژه بدون لایسنس مشخص منتشر شده است. در صورت نیاز، لایسنس مدنظر خود را اضافه کنید.
