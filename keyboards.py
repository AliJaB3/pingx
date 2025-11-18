from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_main(uid: int, is_admin: bool):
    btns = [
        [
            InlineKeyboardButton(text="کیف پول", callback_data="wallet"),
            InlineKeyboardButton(text="خرید پلن", callback_data="buy"),
        ],
        [InlineKeyboardButton(text="اشتراک‌های من", callback_data="mysubs")],
        [InlineKeyboardButton(text="پشتیبانی", callback_data="support")],
    ]
    if is_admin:
        btns.insert(0, [InlineKeyboardButton(text="ادمین", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def kb_force_join(channel: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="عضویت در کانال",
                    url=f"https://t.me/{channel.lstrip('@')}",
                )
            ],
            [InlineKeyboardButton(text="بررسی عضویت", callback_data="recheck_join")],
        ]
    )


def kb_plans(plans, is_admin: bool):
    rows = []
    for p in plans:
        import json

        flags = json.loads(p.get("flags") or "{}")
        if flags.get("admin_only") and not is_admin:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{p['title']} | {p['price']:,} تومان",
                    callback_data=f"plan:{p['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="بازگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_mysubs(rows):
    kb = [
        [
            InlineKeyboardButton(
                text=f"اشتراک #{r['id']} | {r['plan_id']} | {r['price']:,} تومان",
                callback_data=f"sub:{r['id']}",
            )
        ]
        for r in rows
    ]
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def kb_sub_detail(purchase_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="نمایش لینک/QR", callback_data=f"subfix:{purchase_id}")],
            [InlineKeyboardButton(text="لغو اشتراک", callback_data=f"subrevoke:{purchase_id}")],
            [InlineKeyboardButton(text="آمار مصرف", callback_data=f"substat:{purchase_id}")],
            [InlineKeyboardButton(text="بازگشت", callback_data="mysubs")],
        ]
    )


def kb_admin_root():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="پرداخت‌های معلق", callback_data="admin:pending:0")],
            [InlineKeyboardButton(text="کاربران", callback_data="admin:users:0:")],
            [InlineKeyboardButton(text="تیکت‌ها", callback_data="admin:tickets:0")],
            [InlineKeyboardButton(text="پلن‌ها", callback_data="admin:plans")],
            [InlineKeyboardButton(text="قالب پیام", callback_data="admin:templates")],
            [InlineKeyboardButton(text="تنظیمات", callback_data="admin:settings")],
            [InlineKeyboardButton(text="تست اتصال 3x-ui", callback_data="admin:paneltest")],
            [InlineKeyboardButton(text="بازگشت", callback_data="home")],
        ]
    )
