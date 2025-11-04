from aiogram import Router, F
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InputFile, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_IDS, REQUIRED_CHANNEL, CARD_NUMBER, MAX_RECEIPT_MB, MAX_RECEIPT_PHOTOS
from keyboards import kb_main, kb_force_join, kb_plans, kb_mysubs, kb_sub_detail
from db import (save_or_update_user, db_get_wallet, db_get_plans_for_user, db_get_plan, try_deduct_wallet, rollback_wallet,
                db_new_purchase, user_purchases, cache_get_usage, set_setting, get_setting, cur, log_evt)
from utils import htmlesc, progress_bar, human_bytes, qr_bytes, safe_name_from_user
from xui import three_session
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from config import THREEXUI_INBOUND_ID, SUB_PATH, SUB_PORT, SUB_SCHEME, SUB_HOST

import secrets, re, json
from datetime import datetime, timezone
TZ=timezone.utc

router = Router()

class Topup(StatesGroup): amount=State(); note=State()

def build_subscribe_url(sub_id:str)->str:
    host = SUB_HOST or (three_session and three_session.base.split(\"://\")[-1].split(\":\")[0]) or \"localhost\"
    path=SUB_PATH if SUB_PATH.endswith(\"/\") else (SUB_PATH + \"/\")
    return f\"{SUB_SCHEME}://{host}:{SUB_PORT}{path}{sub_id}\"

async def check_force_join(bot, uid:int)->bool:
    ch=get_setting(\"REQUIRED_CHANNEL\", REQUIRED_CHANNEL)
    if not ch: return True
    try:
        cm=await bot.get_chat_member(ch, uid)
        return cm.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except:
        return True

@router.message(CommandStart())
async def start(m:Message):
    save_or_update_user(m.from_user)
    if not await check_force_join(m.bot, m.from_user.id):
        await m.answer(\"برای استفاده از ربات ابتدا عضو کانال شوید.\", reply_markup=kb_force_join(get_setting(\"REQUIRED_CHANNEL\", REQUIRED_CHANNEL)))
        return
    bal=db_get_wallet(m.from_user.id)
    welcome=get_setting(\"WELCOME_TEMPLATE\",\"\")
    await m.answer(welcome+f\"\\n\\n💼 موجودی: <b>{bal:,} تومان</b>\", reply_markup=kb_main(m.from_user.id, m.from_user.id in ADMIN_IDS))

@router.callback_query(F.data==\"home\")
async def home(cb:CallbackQuery):
    bal=db_get_wallet(cb.from_user.id)
    welcome=get_setting(\"WELCOME_TEMPLATE\",\"\")
    await cb.message.edit_text(welcome+f\"\\n\\n💼 موجودی: <b>{bal:,} تومان</b>\", reply_markup=kb_main(cb.from_user.id, cb.from_user.id in ADMIN_IDS))

@router.callback_query(F.data==\"buy\")
async def buy_menu(cb:CallbackQuery):
    plans=db_get_plans_for_user(cb.from_user.id in ADMIN_IDS)
    await cb.message.edit_text(\"یکی از پلن‌ها را انتخاب کنید:\", reply_markup=kb_plans(plans, cb.from_user.id in ADMIN_IDS))

@router.callback_query(F.data.startswith(\"plan:\"))
async def plan_select(cb:CallbackQuery):
    pid=cb.data.split(\":\")[1]; plan=db_get_plan(pid)
    if not plan: return await cb.answer(\"پلن نامعتبر\")
    price=plan['price']; bal=db_get_wallet(cb.from_user.id)
    if bal<price:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"➕ شارژ کیف پول\", callback_data=\"topup\")],[InlineKeyboardButton(text=\"⬅️ برگشت\", callback_data=\"buy\")]])
        await cb.message.edit_text(f\"❗️ موجودی کافی نیست. قیمت: <b>{price:,}</b> — موجودی: <b>{bal:,}</b>\", reply_markup=kb, parse_mode=ParseMode.HTML); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"✅ تایید خرید\", callback_data=f\"confirm:{pid}\")],[InlineKeyboardButton(text=\"⬅️ برگشت\", callback_data=\"buy\")]])
    await cb.message.edit_text(f\"تایید خرید: <b>{plan['title']}</b> — مبلغ: <b>{price:,} تومان</b>\", reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith(\"confirm:\"))
async def buy_confirm(cb:CallbackQuery):
    pid=cb.data.split(\":\")[1]; plan=db_get_plan(pid)
    if not plan: return await cb.answer(\"پلن نامعتبر\")
    price=plan[\"price\"]
    if not try_deduct_wallet(cb.from_user.id, price): return await cb.answer(\"موجودی کافی نیست.\")
    if not three_session:
        rollback_wallet(cb.from_user.id, price); await cb.message.edit_text(\"❌ اتصال به پنل تنظیم نشده است.\"); return
    inbound_id=int(get_setting(\"ACTIVE_INBOUND_ID\", str(THREEXUI_INBOUND_ID)))
    email=safe_name_from_user(cb.from_user); remark=f\"{(cb.from_user.full_name or cb.from_user.username or cb.from_user.id)} | {cb.from_user.id}\"
    try:
        added=await three_session.add_client(inbound_id, email=email, expire_days=plan[\"days\"], data_gb=plan[\"gb\"], remark=remark)
        client=added[\"client\"]; client_id=client[\"id\"]; sub_id=client.get(\"subId\") or secrets.token_hex(6)
        if not client.get(\"subId\"):
            c2=dict(client); c2[\"subId\"]=sub_id
            await three_session.update_client(inbound_id, client_id, c2)
        sub_link=build_subscribe_url(sub_id)
        expiry_ms=int(client.get(\"expiryTime\") or 0)
        allocated_gb=int(plan[\"gb\"] or 0)
    except Exception as e:
        rollback_wallet(cb.from_user.id, price)
        await cb.message.edit_text(f\"❌ ساخت کلاینت انجام نشد:\\n<code>{htmlesc(str(e))}</code>\", parse_mode=ParseMode.HTML)
        return
    pid2=db_new_purchase(user_id=cb.from_user.id, plan_id=plan[\"id\"], price=price,
                    three_xui_client_id=client_id, three_xui_inbound_id=str(inbound_id),
                    client_email=email, sub_id=sub_id, sub_link=sub_link,
                    allocated_gb=allocated_gb, expiry_ms=expiry_ms, meta=None)
    try:
        await cb.bot.send_photo(cb.from_user.id, InputFile(qr_bytes(sub_link), filename=\"pingx.png\"), caption=\"✅ اشتراک شما فعال شد. QR را اسکن کنید.\")
        await cb.bot.send_message(cb.from_user.id, f\"🔗 <a href=\\\"{htmlesc(sub_link)}\\\">Open Subscribe</a>\\n<code>{sub_link}</code>\", parse_mode=ParseMode.HTML)
    except: pass
    extra=get_setting(\"POST_PURCHASE_TEMPLATE\",\"\").strip()
    if extra: await cb.bot.send_message(cb.from_user.id, extra)
    log_evt(cb.from_user.id,\"purchase_confirm\",{\"purchase_id\":pid2,\"plan_id\":plan[\"id\"],\"inbound_id\":inbound_id})
    await cb.message.edit_text(\"اشتراک شما ثبت شد. از «اشتراک‌های من» می‌توانید مدیریت کنید.\", reply_markup=kb_main(cb.from_user.id, cb.from_user.id in ADMIN_IDS))

@router.callback_query(F.data==\"mysubs\")
async def mysubs(cb:CallbackQuery):
    rows=user_purchases(cb.from_user.id)
    if not rows:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"🛒 خرید اشتراک\", callback_data=\"buy\")],[InlineKeyboardButton(text=\"⬅️ برگشت\", callback_data=\"home\")]])
        await cb.message.edit_text(\"هنوز خریدی ندارید.\", reply_markup=kb); return
    await cb.message.edit_text(\"خریدهای شما:\", reply_markup=kb_mysubs(rows))

@router.callback_query(F.data.startswith(\"sub:\"))
async def sub_detail(cb:CallbackQuery):
    pid=int(cb.data.split(\":\")[1]); r=cur.execute(\"SELECT * FROM purchases WHERE id=?\", (pid,)).fetchone()
    if not r or r[\"user_id\"]!=cb.from_user.id: return await cb.answer(\"یافت نشد\")
    cached=cache_get_usage(pid)
    usage_txt=\"⏳ برای مشاهده مصرف، «🔄 بروزرسانی مصرف» را بزنید.\"
    if cached:
        up,down,total,expiry=int(cached[\"up\"] or 0), int(cached[\"down\"] or 0), int(cached[\"total\"] or 0), int(cached[\"expiry_ms\"] or 0)
        used=up+down; pct=0.0 if total<=0 else min(1.0, used/total); bar=progress_bar(pct)
        total_hr=\"نامحدود\" if total<=0 else human_bytes(total)
        exp_txt=datetime.fromtimestamp((expiry or r[\"expiry_ms\"] or 0)/1000, TZ).strftime('%Y-%m-%d %H:%M') if (expiry or r[\"expiry_ms\"]) else \"-\"
        usage_txt=f\"🔋 مصرف: {human_bytes(used)} / {total_hr} ({int(pct*100)}%)\\n{bar}\\n⏳ انقضا: {exp_txt}\"
    text=(f\"<b>خرید #{r['id']}</b>\\nپلن: {r['plan_id']} | مبلغ: {r['price']:,}\\n\"
          f\"اینباند: {r['three_xui_inbound_id']}\\nکلاینت: <code>{r['three_xui_client_id']}</code>\\n\"
          f\"SubId: <code>{r['sub_id'] or '-'}</code>\\n\\n{usage_txt}\")
    await cb.message.edit_text(text, reply_markup=kb_sub_detail(pid), parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith(\"subfix:\"))
async def sub_fix_link(cb:CallbackQuery):
    pid=int(cb.data.split(\":\")[1]); r=cur.execute(\"SELECT * FROM purchases WHERE id=?\", (pid,)).fetchone()
    if not r or r[\"user_id\"]!=cb.from_user.id: return await cb.answer(\"یافت نشد\")
    link=build_subscribe_url(r[\"sub_id\"]) if r[\"sub_id\"] else r[\"sub_link\"]
    try:
        await cb.bot.send_photo(cb.from_user.id, InputFile(qr_bytes(link), filename=f\"pingx-{pid}.png\"), caption=\"🔗 لینک/QR شما:\")
    except: pass
    await cb.bot.send_message(cb.from_user.id, f\"<a href=\\\"{htmlesc(link)}\\\">Open Subscribe</a>\\n<code>{link}</code>\", parse_mode=ParseMode.HTML)
    await cb.answer(\"ارسال شد\")

@router.callback_query(F.data.startswith(\"subrevoke:\"))
async def sub_revoke(cb:CallbackQuery):
    from xui import three_session
    if not three_session: return await cb.answer(\"تنظیمات پنل ناقص است.\", show_alert=True)
    pid=int(cb.data.split(\":\")[1]); r=cur.execute(\"SELECT * FROM purchases WHERE id=?\", (pid,)).fetchone()
    if not r or r[\"user_id\"]!=cb.from_user.id: return await cb.answer(\"یافت نشد\")
    inbound_id=int(r[\"three_xui_inbound_id\"]); client_id=r[\"three_xui_client_id\"]
    try:
        new_subid=await three_session.rotate_subid(inbound_id, client_id)
        new_link=build_subscribe_url(new_subid)
        cur.execute(\"UPDATE purchases SET sub_id=?, sub_link=? WHERE id=?\", (new_subid,new_link,pid))
        await cb.bot.send_message(cb.from_user.id, f\"♻️ لینک شما ریووک شد:\\n<a href=\\\"{htmlesc(new_link)}\\\">Open Subscribe</a>\\n<code>{new_link}</code>\", parse_mode=ParseMode.HTML)
        await cb.answer(\"انجام شد\")
    except Exception as e:
        msg=str(e); msg=(msg[:180]+\"…\") if len(msg)>180 else msg
        await cb.answer(f\"خطا: {msg}\", show_alert=True)

@router.callback_query(F.data.startswith(\"substat:\"))
async def sub_stat_refresh(cb:CallbackQuery):
    from xui import three_session
    from db import cache_set_usage
    if not three_session: return await cb.answer(\"اتصال پنل برقرار نیست.\", show_alert=True)
    pid=int(cb.data.split(\":\")[1]); r=cur.execute(\"SELECT * FROM purchases WHERE id=?\", (pid,)).fetchone()
    if not r or r[\"user_id\"]!=cb.from_user.id: return await cb.answer(\"یافت نشد\")
    inbound_id=int(r[\"three_xui_inbound_id\"]); client_id=r[\"three_xui_client_id\"]
    stat=await three_session.get_client_stats(inbound_id, client_id, r[\"client_email\"])
    if not stat: return await cb.answer(\"آمار یافت نشد.\", show_alert=True)
    total=int(stat.get(\"total\") or 0)
    if total<=0 and int(r[\"allocated_gb\"] or 0)>0: total=int(r[\"allocated_gb\"])*1024**3
    expiry=int(stat.get(\"expiryTime\") or r[\"expiry_ms\"] or 0)
    cache_set_usage(pid, int(stat.get(\"up\") or 0), int(stat.get(\"down\") or 0), total, expiry)
    await cb.answer(\"به‌روزرسانی شد\"); await sub_detail(cb)
