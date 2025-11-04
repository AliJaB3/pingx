from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def kb_main(uid:int, is_admin:bool):
    btns=[
        [InlineKeyboardButton(text="💼 کیف پول", callback_data="wallet"),
         InlineKeyboardButton(text="🛒 خرید اشتراک", callback_data="buy")],
        [InlineKeyboardButton(text="📄 اشتراک‌های من", callback_data="mysubs")],
        [InlineKeyboardButton(text="🎁 تست ۱روزه", callback_data="trial:start")],
        [InlineKeyboardButton(text="🆘 پشتیبانی", callback_data="support")],
    ]
    if is_admin:
        btns.insert(0,[InlineKeyboardButton(text="🛠 مدیریت PingX", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def kb_force_join(channel:str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="عضو کانال شوید", url=f"https://t.me/{channel.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ عضو شدم", callback_data="recheck_join")]
    ])

def kb_plans(plans, is_admin:bool):
    rows=[]
    for p in plans:
        import json
        flags=json.loads(p.get("flags") or "{}" )
        if flags.get("admin_only") and not is_admin: continue
        rows.append([InlineKeyboardButton(text=f"{p['title']} — {p['price']:,} تومان", callback_data=f"plan:{p['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ برگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_mysubs(rows):
    kb=[[InlineKeyboardButton(text=f"🧾 #{r['id']} | {r['plan_id']} | {r['price']:,}ت", callback_data=f"sub:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="⬅️ برگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def kb_sub_detail(purchase_id:int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 لینک/QR", callback_data=f"subfix:{purchase_id}")],
        [InlineKeyboardButton(text="♻️ ریووک لینک", callback_data=f"subrevoke:{purchase_id}")],
        [InlineKeyboardButton(text="🔄 بروزرسانی مصرف", callback_data=f"substat:{purchase_id}")],
        [InlineKeyboardButton(text="⬅️ خریدها", callback_data="mysubs")]
    ])

def kb_admin_root():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 پرداخت‌ها", callback_data="admin:pending:0")],
        [InlineKeyboardButton(text="👥 کاربران", callback_data="admin:users:0:")],
        [InlineKeyboardButton(text="🎫 تیکت‌ها", callback_data="admin:tickets:0")],
        [InlineKeyboardButton(text="🧩 پلن‌ها", callback_data="admin:plans")],
        [InlineKeyboardButton(text="📝 قالب پیام‌ها", callback_data="admin:templates")],
        [InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="admin:settings")],
        [InlineKeyboardButton(text="🔗 تست پنل", callback_data="admin:paneltest")],
        [InlineKeyboardButton(text="⬅️ خانه", callback_data="home")],
    ])
