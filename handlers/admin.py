import re, json, secrets
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

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
from utils import htmlesc, human_bytes
from xui import three_session
from config import THREEXUI_INBOUND_ID

router = Router()


@router.callback_query(F.data == "admin")
async def admin_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("ط¯ط³طھط±ط³غŒ ط؛غŒط±ظ…ط¬ط§ط²", show_alert=True)
    await cb.message.edit_text("ظ¾ظ†ظ„ ط§ط¯ظ…غŒظ†:", reply_markup=kb_admin_root())


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
                    text=f"{name} ({r['user_id']}) آ· {r['wallet']:,}",
                    callback_data=f"admin:u:{r['user_id']}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="Prev", callback_data=f"admin:users:{page-1}:{q or ''}"
            )
        )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="Next", callback_data=f"admin:users:{page+1}:{q or ''}"
            )
        )
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="Back", callback_data="admin")])
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
        header = f"Users (search: {htmlesc(q)}):"
    else:
        rows, total = list_users_page(offset, limit)
        header = "Users:"
    await cb.message.edit_text(
        header, reply_markup=kb_admin_users_list(rows, page, total, limit, q)
    )


@router.callback_query(F.data.regexp(r"^admin:u:(\d+)$"))
async def admin_user_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:(\d+)$", cb.data).group(1))
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not u:
        return await cb.answer("User not found")
    text = (
        f"<b>User {uid}</b>\n"
        f"Name: {(u['first_name'] or '')} {(u['last_name'] or '')}\n"
        f"Username: @{u['username'] or '-'}\n"
        f"Wallet: {u['wallet']:,}\n"
        f"Created: {u['created_at'][:19].replace('T',' ')}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Purchases", callback_data=f"admin:u:buys:{uid}")],
            [InlineKeyboardButton(text="Usage", callback_data=f"admin:u:usage:{uid}")],
            [InlineKeyboardButton(text="Grant trial7", callback_data=f"admin:u:trial7:{uid}")],
            [
                InlineKeyboardButton(text="+50k", callback_data=f"admin:u:wallet:{uid}:+50000"),
                InlineKeyboardButton(text="-50k", callback_data=f"admin:u:wallet:{uid}:-50000"),
            ],
            [InlineKeyboardButton(text="Back", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- Plans management ---


class PlanNew(StatesGroup):
    waiting = State()


class PlanEdit(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:plans")
async def admin_plans_stub_redirect(cb: CallbackQuery):
    # Backward-compatibility if keyboard still points here
    return await admin_plans(cb)


@router.callback_query(F.data == "admin2:plans")
async def admin_plans(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
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
    kb.append([InlineKeyboardButton(text="Back", callback_data="admin")])
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


@router.message(PlanNew.waiting)
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
        await m.reply("Plan added.")
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
            [InlineKeyboardButton(text="Back", callback_data="admin2:plans")],
        ]
    )


@router.callback_query(F.data.regexp(r"^admin2:plan:([^:]+)$"))
async def admin_plan_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
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
    await state.update_data(pid=pid, field=field)
    await cb.message.edit_text(
        f"Send new value for {field}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:plan:{pid}")]]
        ),
    )


@router.message(PlanEdit.waiting)
async def admin_plan_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("pid")
    field = data.get("field")
    val = (m.text or "").strip()
    try:
        if field in ("days", "gb", "price"):
            val_int = int(val)
            db_update_plan_field(pid, field, val_int)
        else:
            db_update_plan_field(pid, field, val)
        await state.clear()
        await m.reply("Updated.")
    except Exception as e:
        await m.reply(f"Update failed: {e}")


@router.callback_query(F.data.regexp(r"^admin2:plan:flag:([^:]+):(admin_only|test)$"))
async def admin_plan_toggle_flag(cb: CallbackQuery):
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
    await admin_plan_view(cb)


@router.callback_query(F.data.regexp(r"^admin2:plan:del:([^:]+)$"))
async def admin_plan_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    pid = re.match(r"^admin2:plan:del:([^:]+)$", cb.data).group(1)
    db_delete_plan(pid)
    await admin_plans(cb)


# --- Templates management ---


class TemplateEdit(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:templates")
async def admin_templates_stub_redirect(cb: CallbackQuery):
    return await admin_templates(cb)


@router.callback_query(F.data == "admin2:templates")
async def admin_templates(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    w = get_setting("WELCOME_TEMPLATE", "(empty)")
    p = get_setting("POST_PURCHASE_TEMPLATE", "(empty)")
    txt = f"Templates:\nWELCOME_TEMPLATE:\n{w}\n\nPOST_PURCHASE_TEMPLATE:\n{p}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit Welcome", callback_data="admin2:t:edit:WELCOME_TEMPLATE")],
            [InlineKeyboardButton(text="Edit PostPurchase", callback_data="admin2:t:edit:POST_PURCHASE_TEMPLATE")],
            [InlineKeyboardButton(text="Back", callback_data="admin")],
        ]
    )
    await cb.message.edit_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:t:edit:(WELCOME_TEMPLATE|POST_PURCHASE_TEMPLATE)$"))
async def admin_template_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    key = re.match(r"^admin2:t:edit:(WELCOME_TEMPLATE|POST_PURCHASE_TEMPLATE)$", cb.data).group(1)
    await state.set_state(TemplateEdit.waiting)
    await state.update_data(key=key)
    await cb.message.edit_text(
        f"Send new value for {key} (HTML allowed):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:templates")]]
        ),
    )


@router.message(TemplateEdit.waiting)
async def admin_template_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    set_setting(key, m.html_text or m.text or "")
    await state.clear()
    await m.reply("Template updated.")


# --- Settings management ---


class SettingEdit(StatesGroup):
    waiting = State()

class SettingNew(StatesGroup):
    waiting = State()

class TemplateNew(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:settings")
async def admin_settings_stub_redirect(cb: CallbackQuery):
    return await admin_settings(cb)


@router.callback_query(F.data == "admin2:settings")
async def admin_settings(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    active = get_setting("ACTIVE_INBOUND_ID", "-")
    req_ch = get_setting("REQUIRED_CHANNEL", "-")
    card = get_setting("CARD_NUMBER", "-")
    txt = (
        f"Settings:\nACTIVE_INBOUND_ID: {active}\nREQUIRED_CHANNEL: {req_ch}\nCARD_NUMBER: {card}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit ACTIVE_INBOUND_ID", callback_data="admin2:s:edit:ACTIVE_INBOUND_ID")],
            [InlineKeyboardButton(text="Edit REQUIRED_CHANNEL", callback_data="admin2:s:edit:REQUIRED_CHANNEL")],
            [InlineKeyboardButton(text="Edit CARD_NUMBER", callback_data="admin2:s:edit:CARD_NUMBER")],
            [InlineKeyboardButton(text="All Settings", callback_data="admin2:allsettings:0")],
            [InlineKeyboardButton(text="All Templates", callback_data="admin2:alltpl:0")],
            [InlineKeyboardButton(text="Back", callback_data="admin")],
        ]
    )
    await cb.message.edit_text(txt, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^admin2:s:edit:(ACTIVE_INBOUND_ID|REQUIRED_CHANNEL|CARD_NUMBER)$"))
async def admin_settings_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    key = re.match(r"^admin2:s:edit:(ACTIVE_INBOUND_ID|REQUIRED_CHANNEL|CARD_NUMBER)$", cb.data).group(1)
    await state.set_state(SettingEdit.waiting)
    await state.update_data(key=key)
    await cb.message.edit_text(
        f"Send new value for {key}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:settings")]]
        ),
    )


@router.message(SettingEdit.waiting)
async def admin_settings_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    val = (m.text or "").strip()
    if key == "ACTIVE_INBOUND_ID":
        if not val.isdigit():
            return await m.reply("Must be a number.")
    if key == "REQUIRED_CHANNEL":
        if val and not val.startswith("@"):
            val = "@" + val
    set_setting(key, val)
    await state.clear()
    await m.reply("Setting updated.")


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
        kb.append([InlineKeyboardButton(text=f"{r['key']}", callback_data=f"{base_cb}:edit:{r['key']}")])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text="Prev", callback_data=f"{base_cb}:{page-1}"))
    if (page+1)*size<total: nav.append(InlineKeyboardButton(text="Next", callback_data=f"{base_cb}:{page+1}"))
    if nav: kb.append(nav)
    if base_cb.startswith("admin2:alltpl"):
        kb.append([InlineKeyboardButton(text="Add Template", callback_data="admin2:t:add")])
    else:
        kb.append([InlineKeyboardButton(text="Add Setting", callback_data="admin2:s:add")])
    kb.append([InlineKeyboardButton(text="Back", callback_data="admin2:settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.regexp(r"^admin2:allsettings:(\d+)$"))
async def admin_all_settings(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    page=int(re.match(r"^admin2:allsettings:(\d+)$", cb.data).group(1))
    rows,total=list_settings_page(None,None,page,10)
    await cb.message.edit_text("All Settings:", reply_markup=kb_settings_list(rows,page,total,10,"admin2:allsettings"))


@router.callback_query(F.data.regexp(r"^admin2:alltpl:(\d+)$"))
async def admin_all_templates(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    page=int(re.match(r"^admin2:alltpl:(\d+)$", cb.data).group(1))
    rows,total=list_settings_page("key LIKE ?","%TEMPLATE%",page,10)
    await cb.message.edit_text("Templates:", reply_markup=kb_settings_list(rows,page,total,10,"admin2:alltpl"))


@router.callback_query(F.data.regexp(r"^admin2:allsettings:edit:(.+)$"))
async def admin_allsettings_edit(cb:CallbackQuery, state:FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    key=re.match(r"^admin2:allsettings:edit:(.+)$", cb.data).group(1)
    await state.set_state(SettingEdit.waiting)
    await state.update_data(key=key)
    curval = get_setting(key, "")
    await cb.message.edit_text(f"Current value for {key}:\n\n{htmlesc(curval)}\n\nSend new value:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin2:allsettings:0")]]), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:alltpl:edit:(.+)$"))
async def admin_alltpl_edit(cb:CallbackQuery, state:FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    key=re.match(r"^admin2:alltpl:edit:(.+)$", cb.data).group(1)
    await state.set_state(TemplateEdit.waiting)
    await state.update_data(key=key)
    curval = get_setting(key, "")
    await cb.message.edit_text(f"Current value for {key}:\n\n{htmlesc(curval)}\n\nSend new value (HTML allowed):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin2:alltpl:0")]]), parse_mode=ParseMode.HTML)


@router.callback_query(F.data=="admin2:s:add")
async def admin_setting_add(cb:CallbackQuery, state:FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(SettingNew.waiting)
    await cb.message.edit_text("Send new setting as key=value", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin2:allsettings:0")]]))


@router.message(SettingNew.waiting)
async def admin_setting_add_recv(m:Message, state:FSMContext):
    txt=m.text or ""
    if "=" not in txt:
        return await m.reply("Use format key=value")
    k, v = txt.split("=",1)
    k=k.strip(); v=v.strip()
    if not k:
        return await m.reply("Invalid key")
    set_setting(k, v)
    await state.clear()
    await m.reply("Saved.")


@router.callback_query(F.data=="admin2:t:add")
async def admin_template_add(cb:CallbackQuery, state:FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(TemplateNew.waiting)
    await cb.message.edit_text("Send new template as KEY_TEMPLATENAME=value (HTML allowed)", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin2:alltpl:0")]]))


@router.message(TemplateNew.waiting)
async def admin_template_add_recv(m:Message, state:FSMContext):
    txt=m.text or ""
    if "=" not in txt:
        return await m.reply("Use format KEY=value")
    k,v = txt.split("=",1)
    k=k.strip(); v=v.strip()
    if not k or "TEMPLATE" not in k:
        return await m.reply("Key should contain TEMPLATE")
    set_setting(k, v)
    await state.clear(); await m.reply("Saved.")


@router.callback_query(F.data == "admin:paneltest")
async def admin_paneltest(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    if not three_session:
        return await cb.message.edit_text(
            "3x-ui session not configured.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin")]]
            ),
        )
    try:
        ibs = await three_session.list_inbounds()
        await cb.message.edit_text(
            f"Panel reachable. Inbounds: {len(ibs)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin")]]
            ),
        )
    except Exception as e:
        await cb.message.edit_text(
            f"Panel error: {e}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="admin")]]
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

@router.message(AdminEdit.add)
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



