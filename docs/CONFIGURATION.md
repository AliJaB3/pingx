# راهنمای تنظیمات (Configuration)

این پروژه از دو محل تنظیمات استفاده می‌کند:
- متغیرهای محیطی / `.env` برای مقداردهی اولیه (خصوصا اتصال 3x-ui و توکن ربات).
- جدول `settings` (از منوی ادمین) برای تغییر در حال اجرا.

## فهرست کلیدها
| کلید | توضیح | نوع/مقدار | محل تنظیم |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | توکن BotFather | string | `.env` |
| `ADMIN_IDS` | لیست ادمین‌ها (CSV) | عددی CSV | `.env` و از طریق بات (settings ADMIN_IDS) |
| `DB_PATH` | مسیر پایگاه داده SQLite | string | `.env` |
| `THREEXUI_BASE_URL` | آدرس پنل 3x-ui بدون /panel انتهایی | URL | `.env` |
| `THREEXUI_USERNAME` / `THREEXUI_PASSWORD` | کاربر/رمز پنل 3x-ui | string | `.env` |
| `THREEXUI_INBOUND_ID` | شناسه inbound پیش‌فرض | عدد | `.env` |
| `ACTIVE_INBOUND_ID` | inbound فعال برای فروش (قابل تغییر در بات) | عدد/رشته | settings |
| `SUB_HOST` / `SUB_SCHEME` / `SUB_PORT` / `SUB_PATH` | ساخت لینک سابسکریپشن (در صورت خالی، از URL پنل خوانده می‌شود) | string/int | settings (defaults از env) |
| `REQUIRED_CHANNEL` / `REQUIRED_CHANNELS` | کانال(های) اجباری (هندل یا ID) | string / لیست | settings |
| `CARD_NUMBER` | شماره کارت نمایش در پیام‌های شارژ | string | settings (default از env) |
| `MAX_RECEIPT_PHOTOS` | حداکثر تعداد فایل رسید | int | settings (default از env) |
| `MAX_RECEIPT_MB` | حداکثر حجم هر فایل (MB) | int | settings (default از env) |
| `TICKET_GROUP_ID` | گروه مقصد پیام‌های تیکت | int | `.env` |
| `SUPPORT_GROUP_ID` | گروه مقصد پیام‌های رسید (در صورت عدم وجود، از TICKET_GROUP_ID استفاده می‌شود) | int | `.env` |
| `SUPPORT_IDS` | شناسه عددی پشتیبان‌ها (CSV) | CSV | settings (منوی ادمین «مدیریت پشتیبان‌ها») |
| `GLOBAL_DISCOUNT_PERCENT` | درصد تخفیف سراسری ۰..۹۰ | int | settings |
| `WELCOME_TEMPLATE`, `POST_PURCHASE_TEMPLATE`, `PURCHASE_SUCCESS_TEMPLATE`, `PURCHASE_FAILED_TEMPLATE` | قالب پیام‌ها | string (HTML مجاز) | settings |
| `PAYMENT_RECEIPT_TEMPLATE`, `TICKET_OPENED_TEMPLATE`, `TICKET_CLOSED_TEMPLATE` | قالب‌های رسید و تیکت | string | settings |
| `PAGE_SIZE_USERS`, `PAGE_SIZE_PAYMENTS`, `PAGE_SIZE_TICKETS` | اندازه صفحات منوهای ادمین | int | `.env` |

> نکته: در صورت نبود مقدار در settings، مقادیر اولیه از `.env` یا پیش‌فرض کد استفاده می‌شود.

## نمونه `.env`
```env
TELEGRAM_BOT_TOKEN=123456:BOT-TOKEN
ADMIN_IDS=11111111,22222222
DB_PATH=/opt/pingx/bot.db

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

## نکات تنظیمات
- `SUPPORT_IDS` فقط از طریق منوی ادمین → «مدیریت پشتیبان‌ها» ویرایش می‌شود.
- `GLOBAL_DISCOUNT_PERCENT` بر نمایش قیمت و مبلغ کسر از کیف پول اثر می‌گذارد (۰ تا ۹۰).
- برای لینک سابسکریپشن اگر `SUB_HOST` خالی باشد، دامنه از `THREEXUI_BASE_URL` استخراج می‌شود؛ پورت خالی از `SUB_PORT` یا پورت URL استفاده می‌کند.
- در صورت نیاز به چند کانال اجباری، `REQUIRED_CHANNELS` را به صورت لیست جداشده با سطر/کاما پر کنید.
