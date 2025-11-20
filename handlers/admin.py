import re, json, secrets
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from db import is_admin, get_admin_ids
from keyboards import kb_admin_root
from db import (
    cur,
    get_setting, set_setting,
    db_get_plan, db_get_plans_for_user,
    user_purchases, cache_get_usage,
    db_get_wallet, db_add_wallet, log_evt,
    db_list_plans, db_insert_plan, db_update_plan_field, db_delete_plan,
)
from utils import htmlesc, human_bytes, parse_channel_list
from xui import three_session
from config import THREEXUI_INBOUND_ID, PAGE_SIZE_USERS

router = Router()


SETTINGS_MENU = [
    ("CARD_NUMBER", "شماره کارت"),
    ("REQUIRED_CHANNELS", "کانال‌های اجباری"),
]

SETTINGS_KEYS_PATTERN = "|".join(key for key, _ in SETTINGS_MENU)


SETTINGS_META = {
    "REQUIRED_CHANNELS": {"type": "channels"},
}


@router.callback_query(F.data == "admin")
async def admin_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await cb.message.edit_text("پنل ادمین:", reply_markup=kb_admin_root())


def search_users_page(q: str, offset: int, limit: int):
    ql = f"%{q.lower()}%"
    rows = cur.execute(
        """
    SELECT user_id,username,first_name,last_name,wallet,created_at FROM users
    WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(first_name,'')) LIKE ?
       OR lower(COALESCE(last_name,'')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    ORDER BY created_at DESC LIMIT ? OFFSET ?
    """,
        (ql, ql, ql, ql, limit, offset),
    ).fetchall()
    total = cur.execute(
        """
    SELECT COUNT(1) FROM users
    WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(first_name,'')) LIKE ?
       OR lower(COALESCE(last_name,'')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    """,
        (ql, ql, ql, ql),
    ).fetchone()[0]
    return [dict(r) for r in rows], total


def list_users_page(offset: int, limit: int):
    rows = cur.execute(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    total = cur.execute("SELECT COUNT(1) FROM users").fetchone()[0]
    return [dict(r) for r in rows], total


def kb_admin_users_list(rows, page: int, total: int, page_size: int, q: str | None = None):
    kb = []
    for r in rows:
        name = (
            " ".join(
                filter(
                    None,
                    [r["first_name"] or "", r["last_name"] or ""],
                )
            )
            or (r["username"] or str(r["user_id"]))
        ).strip()
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{name} ({r['user_id']}) · {r['wallet']:,}",
                    callback_data=f"admin:u:{r['user_id']}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="قبلی", callback_data=f"admin:users:{page-1}:{q or ''}"
            )
        )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="بعدی", callback_data=f"admin:users:{page+1}:{q or ''}"
            )
        )
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.regexp(r"^admin:users:(\d+):(.*)$"))
async def admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin:users:(\d+):(.*)$", cb.data)
    page = int(m.group(1))
    q = (m.group(2) or "").strip()
    limit = PAGE_SIZE_USERS
    offset = page * limit
    if q:
        rows, total = search_users_page(q, offset, limit)
        header = f"کاربران (جستجو: {htmlesc(q)}):"
    else:
        rows, total = list_users_page(offset, limit)
        header = "کاربران:"
    await cb.message.edit_text(
        header, reply_markup=kb_admin_users_list(rows, page, total, limit, q)
    )


async def _render_admin_user_detail(cb: CallbackQuery, uid: int):
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not u:
        await cb.answer("کاربر یافت نشد", show_alert=True)
        return False
    text = (
        f"<b>کاربر {uid}</b>\n"
        f"نام: {(u['first_name'] or '').strip()} {(u['last_name'] or '').strip()}\n"
        f"یوزرنیم: @{u['username'] or '-'}\n"
        f"موجودی فعلی: {u['wallet']:,}\n"
        f"تاریخ عضویت: {u['created_at'][:19].replace('T',' ')}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="خریدها", callback_data=f"admin:u:buys:{uid}")],
            [InlineKeyboardButton(text="مصرف", callback_data=f"admin:u:usage:{uid}")],
            [InlineKeyboardButton(text="اعطای آزمایشی ۷روزه", callback_data=f"admin:u:trial7:{uid}")],
            [
                InlineKeyboardButton(text="+50k", callback_data=f"admin:u:wallet:{uid}:+50000"),
                InlineKeyboardButton(text="-50k", callback_data=f"admin:u:wallet:{uid}:-50000"),
            ],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return True


@router.callback_query(F.data.regexp(r"^admin:u:(\d+)$"))
async def admin_user_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:(\d+)$", cb.data).group(1))
    await _render_admin_user_detail(cb, uid)


@router.callback_query(F.data.regexp(r"^admin:u:wallet:(\d+):([+-]?\d+)$"))
async def admin_user_wallet_adjust(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin:u:wallet:(\d+):([+-]?\d+)$", cb.data)
    uid = int(m.group(1))
    delta = int(m.group(2))
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not u:
        return await cb.answer("کاربر یافت نشد", show_alert=True)
    db_add_wallet(uid, delta)
    log_evt(cb.from_user.id, "wallet_adjust", {"target": uid, "delta": delta})
    await cb.answer(f"تغییر {delta:+,} ثبت شد.")
    await _render_admin_user_detail(cb, uid)


@router.callback_query(F.data.regexp(r"^admin:u:buys:(\d+)$"))
async def admin_user_buys(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:buys:(\d+)$", cb.data).group(1))
    rows = user_purchases(uid)
    if not rows:
        text = "خریدی برای این کاربر ثبت نشده است."
    else:
        lines = []
        for r in rows[:10]:
            ts = r.get("created_at") or ""
            ts = ts[:19].replace("T", " ") if ts else "-"
            lines.append(
                f"#{r['id']} | پلن {htmlesc(r['plan_id'])} | مبلغ {r['price']:,} | تاریخ {ts}"
            )
        text = "<b>خریدهای اخیر:</b>\n" + "\n".join(lines)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بازگشت به کاربر", callback_data=f"admin:u:{uid}")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin:u:usage:(\d+)$"))
async def admin_user_usage(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:usage:(\d+)$", cb.data).group(1))
    rows = user_purchases(uid)
    if not rows:
        text = "مصرفی برای این کاربر یافت نشد."
    else:
        lines = []
        for r in rows[:5]:
            cached = cache_get_usage(r["id"])
            if cached:
                used = int(cached.get("up") or 0) + int(cached.get("down") or 0)
                total = int(cached.get("total") or 0)
                pct = 0 if total <= 0 else min(99, int((used / total) * 100))
                total_hr = "نامحدود" if total <= 0 else human_bytes(total)
                usage_txt = f"{human_bytes(used)} / {total_hr} ({pct}٪)"
            else:
                usage_txt = "داده‌ای ثبت نشده"
            lines.append(f"#{r['id']} | پلن {htmlesc(r['plan_id'])} | {usage_txt}")
        text = "<b>وضعیت مصرف:</b>\n" + "\n".join(lines)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بازگشت به کاربر", callback_data=f"admin:u:{uid}")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- Plans management ---


class PlanNew(StatesGroup):
    waiting = State()


class PlanEdit(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:plans")
async def admin_plans_stub_redirect(cb: CallbackQuery, state: FSMContext):
    # Backward-compatibility if keyboard still points here
    return await admin_plans(cb, state)


@router.callback_query(F.data == "admin2:plans")
async def admin_plans(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    curr_state = await state.get_state()
    if curr_state in {PlanNew.waiting.state, PlanEdit.waiting.state}:
        await state.clear()
    plans = db_list_plans()
    kb = []
    for p in plans:
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{p['id']} | {p['title']} | {p['price']:,}",
                    callback_data=f"admin2:plan:{p['id']}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="Add Plan", callback_data="admin2:plan:add")])
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    await cb.message.edit_text("Plans:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "admin2:plan:add")
async def admin_plan_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(PlanNew.waiting)
    await cb.message.edit_text(
        "Send new plan as: id | title | days | gb | price",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:plans")]]
        ),
    )


@router.message(StateFilter(PlanNew.waiting))
async def admin_plan_add_recv(m: Message, state: FSMContext):
    if m.text and m.text.strip().lower() == "/cancel":
        await state.clear()
        return await m.reply("Cancelled.")
    try:
        parts = [x.strip() for x in (m.text or "").split("|")]
        if len(parts) != 5:
            raise ValueError("need 5 parts (id|title|days|gb|price)")
        pid, title, days, gb, price = parts
        days = int(days)
        gb = int(gb)
        price = int(price)
        db_insert_plan(pid, title, days, gb, price)
        await state.clear()
        await m.reply(
            "Plan added.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت به لیست پلن‌ها", callback_data="admin2:plans")]]
            ),
        )
    except Exception as e:
        return await m.reply(f"Invalid input: {e}")


def kb_plan_detail(p: dict):
    try:
        flags = json.loads(p.get("flags") or "{}")
    except Exception:
        flags = {}
    admin_only = bool(flags.get("admin_only"))
    test = bool(flags.get("test"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit Title", callback_data=f"admin2:plan:edit:{p['id']}:title")],
            [
                InlineKeyboardButton(text="Edit Days", callback_data=f"admin2:plan:edit:{p['id']}:days"),
                InlineKeyboardButton(text="Edit GB", callback_data=f"admin2:plan:edit:{p['id']}:gb"),
            ],
            [InlineKeyboardButton(text="Edit Price", callback_data=f"admin2:plan:edit:{p['id']}:price")],
            [
                InlineKeyboardButton(
                    text=f"Toggle AdminOnly: {admin_only}",
                    callback_data=f"admin2:plan:flag:{p['id']}:admin_only",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Toggle Test: {test}",
                    callback_data=f"admin2:plan:flag:{p['id']}:test",
                )
            ],
            [InlineKeyboardButton(text="Delete", callback_data=f"admin2:plan:del:{p['id']}")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin2:plans")],
        ]
    )


@router.callback_query(F.data.regexp(r"^admin2:plan:([^:]+)$"))
async def admin_plan_view(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() == PlanEdit.waiting.state:
        await state.clear()
    pid = re.match(r"^admin2:plan:([^:]+)$", cb.data).group(1)
    p = db_get_plan(pid)
    if not p:
        return await cb.answer("Plan not found")
    txt = (
        f"<b>Plan {htmlesc(p['id'])}</b>\n"
        f"Title: {htmlesc(p['title'])}\n"
        f"Days: {p['days']}\n"
        f"GB: {p['gb']}\n"
        f"Price: {p['price']:,}"
    )
    await cb.message.edit_text(
        txt, reply_markup=kb_plan_detail(p), parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^admin2:plan:edit:([^:]+):(title|days|gb|price)$"))
async def admin_plan_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:plan:edit:([^:]+):(title|days|gb|price)$", cb.data)
    pid = m.group(1)
    field = m.group(2)
    await state.set_state(PlanEdit.waiting)
    await state.update_data(pid=pid, field=field, back_cb=f"admin2:plan:{pid}")
    await cb.message.edit_text(
        f"Send new value for {field}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:plan:{pid}")]]
        ),
    )


@router.message(StateFilter(PlanEdit.waiting))
async def admin_plan_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("pid")
    field = data.get("field")
    back_cb = data.get("back_cb", f"admin2:plan:{pid}" if pid else "admin2:plans")
    val = (m.text or "").strip()
    if not val:
        return await m.reply("مقدار خالی مجاز نیست.")
    try:
        if field in ("days", "gb", "price"):
            val_int = int(val)
            db_update_plan_field(pid, field, val_int)
        else:
            db_update_plan_field(pid, field, val)
        await state.clear()
        await m.reply(
            "پلن به‌روزرسانی شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت به پلن", callback_data=back_cb)]]
            ),
        )
    except Exception as e:
        await m.reply(f"Update failed: {e}")


@router.callback_query(F.data.regexp(r"^admin2:plan:flag:([^:]+):(admin_only|test)$"))
async def admin_plan_toggle_flag(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:plan:flag:([^:]+):(admin_only|test)$", cb.data)
    pid = m.group(1)
    flag = m.group(2)
    p = db_get_plan(pid)
    if not p:
        return await cb.answer("Plan not found")
    try:
        flags = json.loads(p.get("flags") or "{}")
    except Exception:
        flags = {}
    flags[flag] = not bool(flags.get(flag))
    db_update_plan_field(pid, "flags", json.dumps(flags, ensure_ascii=False))
    await admin_plan_view(cb, state)


@router.callback_query(F.data.regexp(r"^admin2:plan:del:([^:]+)$"))
async def admin_plan_delete(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    pid = re.match(r"^admin2:plan:del:([^:]+)$", cb.data).group(1)
    db_delete_plan(pid)
    await admin_plans(cb, state)


# --- Templates management ---


class TemplateEdit(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:templates")
async def admin_templates_stub_redirect(cb: CallbackQuery, state: FSMContext):
    return await admin_templates(cb, state)


@router.callback_query(F.data == "admin2:templates")
async def admin_templates(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() in {TemplateEdit.waiting.state, TemplateNew.waiting.state}:
        await state.clear()
    w = get_setting("WELCOME_TEMPLATE", "(empty)")
    p = get_setting("POST_PURCHASE_TEMPLATE", "(empty)")
    txt = f"Templates:\nWELCOME_TEMPLATE:\n{w}\n\nPOST_PURCHASE_TEMPLATE:\n{p}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit Welcome", callback_data="admin2:t:edit:WELCOME_TEMPLATE")],
            [InlineKeyboardButton(text="Edit PostPurchase", callback_data="admin2:t:edit:POST_PURCHASE_TEMPLATE")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")],
        ]
    )
    await cb.message.edit_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:t:edit:(WELCOME_TEMPLATE|POST_PURCHASE_TEMPLATE)$"))
async def admin_template_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    key = re.match(r"^admin2:t:edit:(WELCOME_TEMPLATE|POST_PURCHASE_TEMPLATE)$", cb.data).group(1)
    await state.set_state(TemplateEdit.waiting)
    await state.update_data(key=key, back_cb="admin2:templates")
    await cb.message.edit_text(
        f"Send new value for {key} (HTML allowed):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:templates")]]
        ),
    )


@router.callback_query(F.data.regexp(r"^admin2:alltpl:edit:(\d+):(.+)$"))
async def admin_alltpl_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:alltpl:edit:(\d+):(.+)$", cb.data)
    page = int(m.group(1))
    key = m.group(2)
    await state.set_state(TemplateEdit.waiting)
    await state.update_data(key=key, back_cb=f"admin2:alltpl:{page}")
    await cb.message.edit_text(
        f"Send new value for {key} (HTML allowed):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:alltpl:{page}")]]
        ),
    )


@router.message(StateFilter(TemplateEdit.waiting))
async def admin_template_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    back_cb = data.get("back_cb", "admin2:templates")
    set_setting(key, m.html_text or m.text or "")
    await state.clear()
    await m.reply(
        "Template updated.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]
        ),
    )


# --- Settings management ---


class SettingEdit(StatesGroup):
    waiting = State()

class SettingNew(StatesGroup):
    waiting = State()

class TemplateNew(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:settings")
async def admin_settings_stub_redirect(cb: CallbackQuery, state: FSMContext):
    return await admin_settings(cb, state)


@router.callback_query(F.data == "admin2:settings")
async def admin_settings(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() in {SettingEdit.waiting.state, SettingNew.waiting.state}:
        await state.clear()
    lines = []
    buttons = []
    for key, label in SETTINGS_MENU:
        val = get_setting(key, "-")
        safe_val = htmlesc(str(val if val not in (None, "") else "-"))
        lines.append(f"{label}: <code>{safe_val}</code>")
        buttons.append([InlineKeyboardButton(text=f"ویرایش {label}", callback_data=f"admin2:s:edit:{key}")])
    buttons.append(
        [
            InlineKeyboardButton(text="📋 همه تنظیمات", callback_data="admin2:allsettings:0"),
            InlineKeyboardButton(text="📝 همه قالب‌ها", callback_data="admin2:alltpl:0"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    txt = "تنظیمات کارت بانکی:\n" + "\n".join(lines)
    await cb.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(rf"^admin2:s:edit:({SETTINGS_KEYS_PATTERN})$"))
async def admin_settings_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    key = re.match(rf"^admin2:s:edit:({SETTINGS_KEYS_PATTERN})$", cb.data).group(1)
    await state.set_state(SettingEdit.waiting)
    await state.update_data(key=key, back_cb="admin2:settings")
    await cb.message.edit_text(
        f"Send new value for {key}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:settings")]]
        ),
    )


@router.message(StateFilter(SettingEdit.waiting))
async def admin_settings_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    back_cb = data.get("back_cb", "admin2:settings")
    val = (m.text or "").strip()
    meta = SETTINGS_META.get(key)
    if meta:
        kind = meta.get("type")
        if kind == "int":
            try:
                num = int(val)
            except Exception:
                return await m.reply(meta.get("error", "مقدار باید عددی باشد."))
            if num < meta.get("min", 0):
                return await m.reply(meta.get("error", "مقدار باید بزرگتر از صفر باشد."))
            val = str(num)
        elif kind == "channel":
            val = val or ""
            val = val.strip()
            if val and not val.startswith("@"):
                val = "@" + val
        elif kind == "channels":
            channels = parse_channel_list(val)
            if not channels:
                return await m.reply("حداقل یک کانال وارد کنید (با @ یا لینک).")
            val = "\n".join(channels)
        elif kind == "path":
            val = (val or "/").strip() or "/"
            if not val.startswith("/"):
                val = "/" + val
            if not val.endswith("/"):
                val += "/"
        elif kind == "scheme":
            val = (val or "https").strip().lower() or "https"
    set_setting(key, val)
    await state.clear()
    await m.reply(
        "تنظیم به‌روزرسانی شد.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]
        ),
    )


# ---- Generic Settings Manager ----

def list_settings_page(where_clause:str|None, where_arg:str|None, page:int, size:int):
    off = page*size
    if where_clause:
        rows = cur.execute(f"SELECT key,value FROM settings WHERE {where_clause} ORDER BY key LIMIT ? OFFSET ?", (where_arg, size, off) if where_arg is not None else (size, off)).fetchall()
        total = cur.execute(f"SELECT COUNT(1) FROM settings WHERE {where_clause}", (where_arg,) if where_arg is not None else ()).fetchone()[0]
    else:
        rows = cur.execute("SELECT key,value FROM settings ORDER BY key LIMIT ? OFFSET ?", (size, off)).fetchall()
        total = cur.execute("SELECT COUNT(1) FROM settings").fetchone()[0]
    return [dict(r) for r in rows], total


def kb_settings_list(rows, page:int, total:int, size:int, base_cb:str):
    kb=[]
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['key']}", callback_data=f"{base_cb}:edit:{page}:{r['key']}")])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"{base_cb}:{page-1}"))
    if (page+1)*size<total: nav.append(InlineKeyboardButton(text="بعدی", callback_data=f"{base_cb}:{page+1}"))
    if nav: kb.append(nav)
    add_cb = f"{base_cb}:add:{page}"
    if base_cb.startswith("admin2:alltpl"):
        kb.append([InlineKeyboardButton(text="Add Template", callback_data=add_cb)])
    else:
        kb.append([InlineKeyboardButton(text="Add Setting", callback_data=add_cb)])
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin2:settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.regexp(r"^admin2:allsettings:(\d+)$"))
async def admin_all_settings(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    curr_state = await state.get_state()
    if curr_state in {SettingNew.waiting.state, SettingEdit.waiting.state}:
        await state.clear()
    page = int(re.match(r"^admin2:allsettings:(\d+)$", cb.data).group(1))
    size = 10
    rows, total = list_settings_page(None, None, page, size)
    if rows:
        lines = [
            f"{idx+1+page*size}. <b>{htmlesc(r['key'])}</b>: <code>{htmlesc(str(r.get('value') or ''))}</code>"
            for idx, r in enumerate(rows)
        ]
        text = "📋 تمام تنظیمات ذخیره شده:\n" + "\n".join(lines)
    else:
        text = "تنظیمی برای نمایش وجود ندارد."
    kb = kb_settings_list(rows, page, total, size, "admin2:allsettings")
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:alltpl:(\d+)$"))
async def admin_all_templates(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    curr_state = await state.get_state()
    if curr_state in {TemplateNew.waiting.state, TemplateEdit.waiting.state}:
        await state.clear()
    page = int(re.match(r"^admin2:alltpl:(\d+)$", cb.data).group(1))
    size = 10
    rows, total = list_settings_page("key LIKE ?", "%_TEMPLATE", page, size)
    if rows:
        lines = [
            f"{idx+1+page*size}. <b>{htmlesc(r['key'])}</b>: <code>{htmlesc(str(r.get('value') or ''))}</code>"
            for idx, r in enumerate(rows)
        ]
        text = "📝 قالب‌های ذخیره شده:\n" + "\n".join(lines)
    else:
        text = "قالبی برای نمایش وجود ندارد."
    kb = kb_settings_list(rows, page, total, size, "admin2:alltpl")
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:allsettings:edit:(\d+):(.+)$"))
async def admin_allsettings_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:allsettings:edit:(\d+):(.+)$", cb.data)
    page = int(m.group(1))
    key = m.group(2)
    await state.set_state(SettingEdit.waiting)
    await state.update_data(key=key, back_cb=f"admin2:allsettings:{page}")
    await cb.message.edit_text(
        f"Send new value for {key}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:allsettings:{page}")]]
        ),
    )


@router.callback_query(F.data.regexp(r"^admin2:allsettings:add:(\d+)$"))
async def admin_setting_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    page = int(re.match(r"^admin2:allsettings:add:(\d+)$", cb.data).group(1))
    await state.set_state(SettingNew.waiting)
    await state.update_data(back_cb=f"admin2:allsettings:{page}")
    await cb.message.edit_text(
        "کلید و مقدار را به صورت KEY=VALUE ارسال کن (کلید فقط حروف بزرگ/عدد/خط زیر).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="انصراف", callback_data=f"admin2:allsettings:{page}")]]
        ),
    )


@router.message(StateFilter(SettingNew.waiting))
async def admin_setting_add_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    back_cb = data.get("back_cb", "admin2:allsettings:0")
    text = (m.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        return await m.reply("لغو شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]))
    if "=" not in text:
        return await m.reply("فرمت باید KEY=VALUE باشد.")
    key, value = text.split("=", 1)
    key = key.strip().upper()
    value = value.strip()
    if not re.fullmatch(r"[A-Z0-9_]+", key):
        return await m.reply("کلید فقط باید شامل حروف بزرگ، اعداد و '_' باشد.")
    set_setting(key, value)
    await state.clear()
    await m.reply(
        "تنظیم جدید ذخیره شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]),
    )


@router.callback_query(F.data.regexp(r"^admin2:alltpl:add:(\d+)$"))
async def admin_template_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    page = int(re.match(r"^admin2:alltpl:add:(\d+)$", cb.data).group(1))
    await state.set_state(TemplateNew.waiting)
    await state.update_data(back_cb=f"admin2:alltpl:{page}")
    await cb.message.edit_text(
        "خط اول را با کلید (مثلاً WELCOME_TEMPLATE) و خطوط بعدی را با متن قالب (با پشتیبانی از HTML) ارسال کن.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="انصراف", callback_data=f"admin2:alltpl:{page}")]]
        ),
    )


@router.message(StateFilter(TemplateNew.waiting))
async def admin_template_add_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    back_cb = data.get("back_cb", "admin2:alltpl:0")
    plain = (m.text or "").strip()
    if plain.lower() == "/cancel":
        await state.clear()
        return await m.reply("لغو شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]))
    text_plain = m.text or ""
    parts_plain = text_plain.split("\n", 1)
    key = parts_plain[0].strip().upper()
    html_payload = m.html_text or m.text or ""
    parts_html = html_payload.split("\n", 1)
    value = parts_html[1] if len(parts_html) > 1 else ""
    if not key:
        return await m.reply("کلید نمی‌تواند خالی باشد.")
    if not key.endswith("_TEMPLATE"):
        return await m.reply("کلید قالب باید با _TEMPLATE پایان یابد.")
    if not value:
        return await m.reply("متن قالب را وارد کن.")
    set_setting(key, value)
    await state.clear()
    await m.reply(
        "قالب جدید ذخیره شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]),
    )


@router.callback_query(F.data == "admin:paneltest")
async def admin_paneltest(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    if not three_session:
        return await cb.message.edit_text(
            "3x-ui session not configured.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]
            ),
        )
    try:
        ibs = await three_session.list_inbounds()
        await cb.message.edit_text(
            f"Panel reachable. Inbounds: {len(ibs)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]
            ),
        )
    except Exception as e:
        await cb.message.edit_text(
            f"Panel error: {e}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]
            ),
        )




# --- Admins management ---
class AdminEdit(StatesGroup):
    add = State()
    remove = State()

def kb_admins(ids:list[int], page:int=0, page_size:int=20):
    start=page*page_size; chunk=ids[start:start+page_size]
    kb=[]
    for uid in chunk:
        kb.append([InlineKeyboardButton(text=f"🛡️ {uid}", callback_data=f"admin:admins:remove:{uid}")])
    kb.append([InlineKeyboardButton(text="➕ افزودن ادمین", callback_data="admin:admins:add")])
    kb.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data.regexp(r"^admin:admins:(\d+)$"))
async def admin_admins_list(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    page=int(re.match(r"^admin:admins:(\d+)$", cb.data).group(1))
    ids=sorted(list(get_admin_ids()))
    await cb.message.edit_text("مدیریت ادمین‌ها:", reply_markup=kb_admins(ids,page))

@router.callback_query(F.data=="admin:admins:add")
async def admin_admins_add(cb:CallbackQuery, state:FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(AdminEdit.add)
    await cb.message.edit_text("آیدی عددی تلگرام کاربر را ارسال کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:admins:0")]]))

@router.message(StateFilter(AdminEdit.add))
async def admin_admins_add_recv(m:Message, state:FSMContext):
    try:
        uid=int(str(m.text).strip())
    except:
        return await m.reply("عدد نامعتبر است.")
    add_admin(uid)
    await state.clear()
    await m.reply(f"کاربر {uid} به فهرست ادمین‌ها افزوده شد.")

@router.callback_query(F.data.regexp(r"^admin:admins:remove:(\d+)$"))
async def admin_admins_remove(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    uid=int(re.match(r"^admin:admins:remove:(\d+)$", cb.data).group(1))
    remove_admin(uid)
    await cb.answer("حذف شد")
    await admin_admins_list(cb)




@router.callback_query(F.data=="admin:dashboard")
async def admin_dashboard(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    # Users stats (last 7 days)
    import datetime as dt
    from collections import Counter
    rows = cur.execute("SELECT created_at FROM users").fetchall()
    days = [(str(r[0])[:10]) for r in rows]
    cnt = Counter(days)
    today = dt.date.today()
    labels = []
    values = []
    for i in range(6,-1,-1):
        d = (today - dt.timedelta(days=i)).isoformat()
        labels.append(d[5:])
        values.append(cnt.get(d,0))
    total_users = cur.execute("SELECT COUNT(1) FROM users").fetchone()[0]
    # خریدها last 7 days
    rows2 = cur.execute("SELECT created_at FROM purchases").fetchall()
    days2 = [(str(r[0])[:10]) for r in rows2]
    cnt2 = Counter(days2)
    pvals = []
    for i in range(6,-1,-1):
        d=(today-dt.timedelta(days=i)).isoformat(); pvals.append(cnt2.get(d,0))
    # Top consumers
    top = cur.execute("""
        SELECT p.user_id, COALESCE(SUM(c.up + c.down),0) AS used
        FROM purchases p LEFT JOIN cache_usage c ON c.purchase_id = p.id
        GROUP BY p.user_id ORDER BY used DESC LIMIT 5
    """).fetchall()
    top_lines = []
    for t in top:
        try:
            uid=int(t[0]); used=int(t[1] or 0)
            top_lines.append(f"{uid}: {human_bytes(used)}")
        except:
            continue
    # Inbounds status
    ib_txt = "پیکربندی نشده"
    try:
        if three_session:
            ibs = await three_session.list_inbounds()
            ib_txt = f"تعداد این‌باندها: {len(ibs)}"
    except Exception as e:
        ib_txt = f"خطا در دریافت این‌باندها: {e}"
    # Compose
    chart_users = " ".join([f"{l}:{v}" for l,v in zip(labels, values)])
    chart_pur = " ".join([f"{l}:{v}" for l,v in zip(labels, pvals)])
    text = (
        "<b>📊 داشبورد</b>\n\n"
        f"👥 کاربران کل: <b>{total_users}</b>\n"
        f"کاربران ۷ روز اخیر: {chart_users}\n"
        f"خریدها ۷ روز اخیر: {chart_pur}\n\n"
        f"🔥 کاربران پرمصرف:\n" + ("\n".join(top_lines) or "-") + "\n\n"
        f"🛰️ وضعیت این‌باندها: {ib_txt}"
    )
    await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")]]))

