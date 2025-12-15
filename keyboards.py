from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_main(uid: int, is_admin: bool, is_support: bool = False):
    btns = [
        [
            InlineKeyboardButton(text="💰 کیف پول", callback_data="wallet"),
            InlineKeyboardButton(text="🛒 خرید پلن", callback_data="buy"),
        ],
        [InlineKeyboardButton(text="📜 اشتراک‌های من", callback_data="mysubs")],
        [InlineKeyboardButton(text="🆘 پشتیبانی", callback_data="support")],
    ]
    if is_admin:
        btns.insert(0, [InlineKeyboardButton(text="🛠 ادمین", callback_data="admin")])
    elif is_support:
        btns.insert(0, [InlineKeyboardButton(text="🎧 پشتیبان", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def kb_force_join(channels):
    rows = []
    for item in channels:
        if isinstance(item, dict):
            label = item.get("label") or "کانال"
            url = item.get("url")
        else:
            label = str(item)
            url = f"https://t.me/{label.lstrip('@')}" if str(label).startswith("@") else None
        button_text = f"عضویت در {label}"
        if url:
            rows.append([InlineKeyboardButton(text=button_text, url=url)])
    rows.append([InlineKeyboardButton(text="🔄 بررسی عضویت", callback_data="recheck_join")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_plans(plans, is_admin: bool, discount_pct: int = 0):
    rows = []
    pct = 0
    try:
        pct = max(0, min(90, int(discount_pct or 0)))
    except Exception:
        pct = 0
    for p in plans:
        import json

        flags = json.loads(p.get("flags") or "{}")
        if flags.get("admin_only") and not is_admin:
            continue
        price = int(p.get("price") or 0)
        final_price = int(price * (100 - pct) / 100) if pct > 0 else price
        price_txt = f"{final_price:,} تومان"
        if pct > 0:
            price_txt += f" ({price:,})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{p['title']} • {price_txt}",
                    callback_data=f"plan:{p['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_mysubs(rows):
    kb = [
        [
            InlineKeyboardButton(
                text=f"اشتراک #{r['id']} • {r['plan_id']} • {r['price']:,} تومان",
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
            [InlineKeyboardButton(text="📎 نمایش لینک فعلی", callback_data=f"sublink:{purchase_id}")],
            [InlineKeyboardButton(text="♻️ صدور لینک جدید/QR", callback_data=f"subfix:{purchase_id}")],
            [InlineKeyboardButton(text="📊 آمار مصرف", callback_data=f"substat:{purchase_id}")],
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="mysubs")],
        ]
    )


def kb_admin_root(is_admin: bool = True, is_support: bool = False):
    rows = [
        [InlineKeyboardButton(text="🧾 پرداخت‌های معلق", callback_data="admin:pending:0")],
        [InlineKeyboardButton(text="🎫 تیکت‌ها", callback_data="admin:tickets:0")],
    ]
    if is_admin:
        rows.extend(
            [
                [InlineKeyboardButton(text="👥 کاربران", callback_data="admin:users:0:")],
                [InlineKeyboardButton(text="📦 پلن‌ها", callback_data="admin:plans")],
                [InlineKeyboardButton(text="📈 گزارش‌ها", callback_data="admin:reports")],
                [InlineKeyboardButton(text="🎁 تخفیف سراسری", callback_data="admin:discount")],
                [InlineKeyboardButton(text="🎧 مدیریت پشتیبان‌ها", callback_data="admin:supports")],
                [InlineKeyboardButton(text="📈 لینک‌های رفرال", callback_data="admin:refs")],
                [InlineKeyboardButton(text="📝 قالب پیام", callback_data="admin:templates")],
                [InlineKeyboardButton(text="Backup", callback_data="admin:backup"), InlineKeyboardButton(text="Restore", callback_data="admin:restore")],
                [InlineKeyboardButton(text="?? ???? ?????", callback_data="admin:settings")],
                [InlineKeyboardButton(text="🔌 تست اتصال 3x-ui", callback_data="admin:paneltest")],
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
