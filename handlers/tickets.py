from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from config import ADMIN_IDS, PAGE_SIZE_TICKETS
from db import (get_or_open_ticket, ticket_close, ticket_set_activity, store_tmsg,
                list_tickets_page, list_ticket_messages_page, cur)
from utils import htmlesc

router = Router()

class AdminReply(StatesGroup):
    waiting = State()

def kb_ticket_user():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=\"🔐 بستن تیکت\", callback_data=\"ticket:close\")],
        [InlineKeyboardButton(text=\"⬅️ بازگشت\", callback_data=\"home\")]
    ])

@router.callback_query(F.data==\"support\")
async def user_support(cb:CallbackQuery):
    trow=cur.execute(\"SELECT id,status FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1\",(cb.from_user.id,)).fetchone()
    if trow:
        await cb.message.edit_text(f\"🎫 تیکت شما باز است: #{trow['id']}\\nپیام خود را ارسال کنید.\", reply_markup=kb_ticket_user())
    else:
        tid=get_or_open_ticket(cb.from_user.id)
        for aid in ADMIN_IDS:
            try: await cb.bot.send_message(aid, f\"🎫 تیکت جدید #{tid} از <a href=\\\"tg://user?id={cb.from_user.id}\\\">{htmlesc(cb.from_user.full_name or cb.from_user.username or str(cb.from_user.id))}</a>\", parse_mode=ParseMode.HTML)
            except: pass
        await cb.message.edit_text(f\"🎫 تیکت شما باز شد: #{tid}\\nپیام خود را ارسال کنید.\", reply_markup=kb_ticket_user())

@router.callback_query(F.data==\"ticket:close\")
async def user_ticket_close(cb:CallbackQuery):
    row=cur.execute(\"SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1\",(cb.from_user.id,)).fetchone()
    if not row: return await cb.answer(\"تیکت بازی ندارید.\", show_alert=True)
    ticket_close(row[\"id\"])
    for aid in ADMIN_IDS:
        try: await cb.bot.send_message(aid, f\"🔒 تیکت #{row['id']} توسط کاربر بسته شد.\")
        except: pass
    await cb.message.edit_text(\"تیکت بسته شد.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"🆘 بازکردن تیکت جدید\", callback_data=\"support\")],[InlineKeyboardButton(text=\"⬅️ خانه\", callback_data=\"home\")]]))

@router.message(StateFilter(None))
async def user_ticket_pipeline(m:Message):
    if m.text and m.text.startswith(\"/\"): return
    t=cur.execute(\"SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1\",(m.from_user.id,)).fetchone()
    if not t: return
    tid=t[\"id\"]; ticket_set_activity(tid)
    header=f\"پیام جدید در تیکت #{tid} از <a href=\\\"tg://user?id={m.from_user.id}\\\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\"
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=\"✉️ پاسخ\", callback_data=f\"adm:tkt:reply:{tid}\"),
         InlineKeyboardButton(text=\"🔒 بستن\", callback_data=f\"adm:tkt:close:{tid}\")],
        [InlineKeyboardButton(text=\"🧵 تاریخچه\", callback_data=f\"adm:tkt:view:{tid}:0\")],
    ])
    sent_ref=None
    for aid in ADMIN_IDS:
        try:
            if m.photo:
                sent_ref=await m.bot.send_photo(aid, m.photo[-1].file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML)
                store_tmsg(tid,\"user\",m.from_user.id,\"photo\",m.photo[-1].file_id, (m.caption or \"\"), sent_ref.message_id)
            elif m.document:
                sent_ref=await m.bot.send_document(aid, m.document.file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML)
                store_tmsg(tid,\"user\",m.from_user.id,\"document\",m.document.file_id, (m.caption or \"\"), sent_ref.message_id)
            elif m.voice:
                sent_ref=await m.bot.send_voice(aid, m.voice.file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML)
                store_tmsg(tid,\"user\",m.from_user.id,\"voice\",m.voice.file_id, (m.caption or \"\"), sent_ref.message_id)
            elif m.video:
                sent_ref=await m.bot.send_video(aid, m.video.file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML)
                store_tmsg(tid,\"user\",m.from_user.id,\"video\",m.video.file_id, (m.caption or \"\"), sent_ref.message_id)
            elif m.sticker:
                sent_ref=await m.bot.send_sticker(aid, m.sticker.file_id)
                store_tmsg(tid,\"user\",m.from_user.id,\"sticker\",m.sticker.file_id, None, sent_ref.message_id)
            else:
                sent_ref=await m.bot.send_message(aid, f\"{header}:\\n{htmlesc(m.text or '').strip()}\", reply_markup=kb, parse_mode=ParseMode.HTML)
                store_tmsg(tid,\"user\",m.from_user.id,\"text\",m.text, None, sent_ref.message_id)
        except: pass

# --- Admin side ---

@router.callback_query(F.data.regexp(r\"^admin:tickets:(\\d+)$\"))
async def admin_tickets_list(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    import re
    page=int(re.match(r\"^admin:tickets:(\\d+)$\", cb.data).group(1))
    rows,total=list_tickets_page(page, PAGE_SIZE_TICKETS)
    kb=[]
    for t in rows:
        kb.append([InlineKeyboardButton(text=f\"#{t['id']} — {t['status']} — user {t['user_id']}\", callback_data=f\"adm:tkt:view:{t['id']}:0\")])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text=\"⬅️ قبلی\", callback_data=f\"admin:tickets:{page-1}\"))
    if (page+1)*PAGE_SIZE_TICKETS<total: nav.append(InlineKeyboardButton(text=\"بعدی ➡️\", callback_data=f\"admin:tickets:{page+1}\"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")])
    await cb.message.edit_text(\"لیست تیکت‌ها:\", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.regexp(r\"^adm:tkt:view:(\\d+):(\\d+)$\"))
async def admin_ticket_view(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    import re
    m=re.match(r\"^adm:tkt:view:(\\d+):(\\d+)$\", cb.data); tid=int(m.group(1)); page=int(m.group(2))
    rows,total=list_ticket_messages_page(tid, page, 10)
    txt=[f\"🧵 تاریخچه تیکت #{tid} (صفحه {page+1})\"]
    for r in rows:
        who = \"👤 کاربر\" if r['sender_type']==\"user\" else \"🛡 ادمین\"
        if r['kind']==\"text\":
            txt.append(f\"{who}: {htmlesc(r['content'] or '')}\")
        else:
            txt.append(f\"{who}: [{r['kind']}] {htmlesc(r.get('caption') or '')}\")
    kb=[[InlineKeyboardButton(text=\"✉️ پاسخ\", callback_data=f\"adm:tkt:reply:{tid}\"),
         InlineKeyboardButton(text=\"🔒 بستن\", callback_data=f\"adm:tkt:close:{tid}\")]]
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text=\"⬅️ قبلی\", callback_data=f\"adm:tkt:view:{tid}:{page-1}\"))
    if (page+1)*10<total: nav.append(InlineKeyboardButton(text=\"بعدی ➡️\", callback_data=f\"adm:tkt:view:{tid}:{page+1}\"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text=\"⬅️ تیکت‌ها\", callback_data=\"admin:tickets:0\")])
    await cb.message.edit_text(\"\\n\".join(txt), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)

@router.callback_query(F.data.regexp(r\"^adm:tkt:close:(\\d+)$\"))
async def admin_ticket_close(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    import re
    tid=int(re.match(r\"^adm:tkt:close:(\\d+)$\", cb.data).group(1))
    row=cur.execute(\"SELECT user_id FROM tickets WHERE id=?\", (tid,)).fetchone()
    if not row: return await cb.answer(\"یافت نشد\")
    ticket_close(tid)
    try: await cb.bot.send_message(row['user_id'], f\"🔒 تیکت #{tid} توسط ادمین بسته شد.\")
    except: pass
    await admin_tickets_list(cb)

@router.callback_query(F.data.regexp(r\"^adm:tkt:reply:(\\d+)$\"))
async def admin_ticket_reply(cb:CallbackQuery, state:FSMContext):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    import re
    tid=int(re.match(r\"^adm:tkt:reply:(\\d+)$\", cb.data).group(1))
    await state.set_state(AdminReply.waiting)
    await state.update_data(tid=tid)
    await cb.message.edit_text(f\"✍️ پیام پاسخ به تیکت #{tid} را ارسال کنید. (متن/عکس/صدا/ویدیو)\\nبرای انصراف /cancel را بفرستید.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ تاریخچه\", callback_data=f\"adm:tkt:view:{tid}:0\")]]))

@router.message(AdminReply.waiting)
async def admin_reply_dispatch(m:Message, state:FSMContext):
    if m.text and m.text.strip()==\"/cancel\": await state.clear(); return await m.reply(\"انصراف داده شد.\")
    data=await state.get_data(); tid=int(data.get(\"tid\"))
    row=cur.execute(\"SELECT user_id FROM tickets WHERE id=?\", (tid,)).fetchone()
    if not row: await state.clear(); return await m.reply(\"تیکت یافت نشد.\")
    uid=row['user_id']
    try:
        if m.photo:
            msg=await m.bot.send_photo(uid, m.photo[-1].file_id, caption=(m.caption or \"\"))
            store_tmsg(tid,\"admin\",m.from_user.id,\"photo\",m.photo[-1].file_id,m.caption or \"\", msg.message_id)
        elif m.document:
            msg=await m.bot.send_document(uid, m.document.file_id, caption=(m.caption or \"\"))
            store_tmsg(tid,\"admin\",m.from_user.id,\"document\",m.document.file_id,m.caption or \"\", msg.message_id)
        elif m.voice:
            msg=await m.bot.send_voice(uid, m.voice.file_id, caption=(m.caption or \"\"))
            store_tmsg(tid,\"admin\",m.from_user.id,\"voice\",m.voice.file_id,m.caption or \"\", msg.message_id)
        elif m.video:
            msg=await m.bot.send_video(uid, m.video.file_id, caption=(m.caption or \"\"))
            store_tmsg(tid,\"admin\",m.from_user.id,\"video\",m.video.file_id,m.caption or \"\", msg.message_id)
        else:
            msg=await m.bot.send_message(uid, m.text or \"\")
            store_tmsg(tid,\"admin\",m.from_user.id,\"text\",m.text or \"\", None, msg.message_id)
    except Exception as e:
        await m.reply(f\"ارسال نشد: {e}\")
        return
    await state.clear()
    await m.reply(\"✅ ارسال شد.\")
