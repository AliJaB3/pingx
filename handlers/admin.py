\
import re, json, secrets
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import ADMIN_IDS, PAGE_SIZE_USERS
from keyboards import kb_admin_root
from db import (cur, get_setting, set_setting, db_get_plan, db_get_plans_for_user, user_purchases,
                cache_get_usage, db_get_wallet, db_add_wallet, log_evt)
from utils import htmlesc, human_bytes
from xui import three_session
from config import THREEXUI_INBOUND_ID

router = Router()

@router.callback_query(F.data==\"admin\")
async def admin_menu(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"دسترسی ندارید\", show_alert=True)
    await cb.message.edit_text(\"بخش مدیریت PingX:\", reply_markup=kb_admin_root())

def search_users_page(q:str, offset:int, limit:int):
    ql=f\"%{q.lower()}%\"
    rows=cur.execute(\"\"\"\
    SELECT user_id,username,first_name,last_name,wallet,created_at FROM users
    WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(first_name,'')) LIKE ?
       OR lower(COALESCE(last_name,'')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    ORDER BY created_at DESC LIMIT ? OFFSET ?
    \"\"\",(ql,ql,ql,ql,limit,offset)).fetchall()
    total=cur.execute(\"\"\"\
    SELECT COUNT(1) FROM users
    WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(first_name,'')) LIKE ?
       OR lower(COALESCE(last_name,'')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    \"\"\",(ql,ql,ql,ql)).fetchone()[0]
    return [dict(r) for r in rows], total

def list_users_page(offset:int, limit:int):
    rows=cur.execute(\"SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?\",(limit,offset)).fetchall()
    total=cur.execute(\"SELECT COUNT(1) FROM users\").fetchone()[0]
    return [dict(r) for r in rows], total

def kb_admin_users_list(rows, page:int, total:int, page_size:int, q:str|None=None):
    kb=[]
    for r in rows:
        name=(\" \".join(filter(None,[r['first_name'] or \"\", r['last_name'] or \"\"])) or (r['username'] or str(r['user_id']))).strip()
        kb.append([InlineKeyboardButton(text=f\"{name} ({r['user_id']}) — {r['wallet']:,}ت\", callback_data=f\"admin:u:{r['user_id']}\")])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text=\"⬅️ قبلی\", callback_data=f\"admin:users:{page-1}:{q or ''}\"))
    if (page+1)*page_size<total: nav.append(InlineKeyboardButton(text=\"بعدی ➡️\", callback_data=f\"admin:users:{page+1}:{q or ''}\"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data.regexp(r\"^admin:users:(\\d+):(.*)$\"))
async def admin_users(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    m=re.match(r\"^admin:users:(\\d+):(.*)$\", cb.data); page=int(m.group(1)); q=(m.group(2) or \"\").strip()
    limit=PAGE_SIZE_USERS; offset=page*limit
    if q:
        rows,total=search_users_page(q, offset, limit); header=f\"کاربران (جستجو: {htmlesc(q)}):\"
    else:
        rows,total=list_users_page(offset,limit); header=\"کاربران:\"
    await cb.message.edit_text(header, reply_markup=kb_admin_users_list(rows,page,total,limit,q))

@router.callback_query(F.data.regexp(r\"^admin:u:(\\d+)$\"))
async def admin_user_detail(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    uid=int(re.match(r\"^admin:u:(\\d+)$\", cb.data).group(1))
    u=cur.execute(\"SELECT * FROM users WHERE user_id=?\", (uid,)).fetchone()
    if not u: return await cb.answer(\"کاربر یافت نشد.\")
    text=(f\"<b>کاربر {uid}</b>\\nنام: {(u['first_name'] or '')} {(u['last_name'] or '')}\\n\"
          f\"یوزرنیم: @{u['username'] or '-'}\\nموجودی: {u['wallet']:,}\\nثبت‌نام: {u['created_at'][:19].replace('T',' ')}\")
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=\"📄 خریدها\", callback_data=f\"admin:u:buys:{uid}\")],
        [InlineKeyboardButton(text=\"🔄 مصرف خریدها\", callback_data=f\"admin:u:usage:{uid}\")],
        [InlineKeyboardButton(text=\"🎁 تست ۷روزه\", callback_data=f\"admin:u:trial7:{uid}\")],
        [InlineKeyboardButton(text=\"💵 +50k\", callback_data=f\"admin:u:wallet:{uid}:+50000\"),
         InlineKeyboardButton(text=\"💵 -50k\", callback_data=f\"admin:u:wallet:{uid}:-50000\")],
        [InlineKeyboardButton(text=\"⬅️ کاربران\", callback_data=\"admin:users:0:\")]
    ])
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# Stubs for menu items to avoid crashes
@router.callback_query(F.data==\"admin:plans\")
async def admin_plans_stub(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return
    await cb.message.edit_text(\"مدیریت پلن‌ها به‌زودی اینجا کامل می‌شود.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]]))

@router.callback_query(F.data==\"admin:templates\")
async def admin_templates_stub(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return
    await cb.message.edit_text(\"ویرایش قالب پیام‌ها به‌زودی.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]]))

@router.callback_query(F.data==\"admin:settings\")
async def admin_settings_stub(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return
    await cb.message.edit_text(\"تنظیمات پیشرفته به‌زودی.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]]))

@router.callback_query(F.data==\"admin:paneltest\")
async def admin_paneltest(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return
    if not three_session:
        return await cb.message.edit_text(\"❌ اتصال پنل تنظیم نیست.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]]))
    try:
        ibs=await three_session.list_inbounds()
        await cb.message.edit_text(f\"✅ اتصال پنل برقرار است. {len(ibs)} اینباند یافت شد.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]]))
    except Exception as e:
        await cb.message.edit_text(f\"❌ خطا در ارتباط: {e}\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]]))
