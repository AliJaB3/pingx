from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from db import is_admin, get_admin_ids
from db import (
    get_or_open_ticket, ticket_close, ticket_set_activity, store_tmsg,
    list_tickets_page, list_ticket_messages_page, cur,
)
from utils import htmlesc
from config import PAGE_SIZE_TICKETS

router = Router()


class AdminReply(StatesGroup):
    waiting = State()


def kb_ticket_user():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بستن تیکت", callback_data="ticket:close")],
            [InlineKeyboardButton(text="بازگشت", callback_data="home")],
        ]
    )


@router.callback_query(F.data == "support")
async def user_support(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    trow = cur.execute(
        "SELECT id,status FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (cb.from_user.id,),
    ).fetchone()
    if trow:
        await cb.message.edit_text(
            f"شما یک تیکت باز دارید: #{trow['id']}\nمی‌توانید پیام جدید بفرستید یا ببندید.",
            reply_markup=kb_ticket_user(),
        )
    else:
        tid = get_or_open_ticket(cb.from_user.id)
        for aid in get_admin_ids():
            try:
                await cb.bot.send_message(
                    aid,
                    f"تیکت جدید #{tid} از <a href=\"tg://user?id={cb.from_user.id}\">{htmlesc(cb.from_user.full_name or cb.from_user.username or str(cb.from_user.id))}</a>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await cb.message.edit_text(
            f"تیکت شما ایجاد شد: #{tid}\nپیام خود را ارسال کنید.",
            reply_markup=kb_ticket_user(),
        )


@router.callback_query(F.data == "ticket:close")
async def user_ticket_close(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    row = cur.execute(
        "SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (cb.from_user.id,),
    ).fetchone()
    if not row:
        return await cb.answer("تیکت باز یافت نشد.", show_alert=True)
    ticket_close(row["id"])
    for aid in get_admin_ids():
        try:
            await cb.bot.send_message(aid, f"تیکت #{row['id']} توسط کاربر بسته شد.")
        except Exception:
            pass
    await cb.message.edit_text(
        "تیکت بسته شد.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="ایجاد تیکت جدید", callback_data="support")],
                [InlineKeyboardButton(text="بازگشت", callback_data="home")],
            ]
        ),
    )


@router.message(StateFilter(None))
async def user_ticket_pipeline(m: Message):
    if getattr(m.chat, "type", "private") != "private":
        return
    if m.text and m.text.startswith("/"):
        return
    t = cur.execute(
        "SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (m.from_user.id,),
    ).fetchone()
    if not t:
        return
    tid = t["id"]
    ticket_set_activity(tid)
    header = (
        f"پیام جدید در تیکت #{tid} از "
        f"<a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="پاسخ", callback_data=f"adm:tkt:reply:{tid}"),
                InlineKeyboardButton(text="بستن", callback_data=f"adm:tkt:close:{tid}"),
            ],
            [InlineKeyboardButton(text="نمایش همه", callback_data=f"adm:tkt:view:{tid}:0")],
        ]
    )
    for aid in get_admin_ids():
        try:
            if m.photo:
                sent = await m.bot.send_photo(
                    aid, m.photo[-1].file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML
                )
                store_tmsg(tid, "user", m.from_user.id, "photo", m.photo[-1].file_id, (m.caption or ""), sent.message_id)
            elif m.document:
                sent = await m.bot.send_document(
                    aid, m.document.file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML
                )
                store_tmsg(tid, "user", m.from_user.id, "document", m.document.file_id, (m.caption or ""), sent.message_id)
            elif m.voice:
                sent = await m.bot.send_voice(
                    aid, m.voice.file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML
                )
                store_tmsg(tid, "user", m.from_user.id, "voice", m.voice.file_id, (m.caption or ""), sent.message_id)
            elif m.video:
                sent = await m.bot.send_video(
                    aid, m.video.file_id, caption=header, reply_markup=kb, parse_mode=ParseMode.HTML
                )
                store_tmsg(tid, "user", m.from_user.id, "video", m.video.file_id, (m.caption or ""), sent.message_id)
            elif m.sticker:
                sent = await m.bot.send_sticker(aid, m.sticker.file_id)
                store_tmsg(tid, "user", m.from_user.id, "sticker", m.sticker.file_id, None, sent.message_id)
            else:
                sent = await m.bot.send_message(aid, f"{header}:\n{htmlesc(m.text or '').strip()}", reply_markup=kb, parse_mode=ParseMode.HTML)
                store_tmsg(tid, "user", m.from_user.id, "text", m.text or "", None, sent.message_id)
        except Exception:
            pass


# --- Admin side ---


@router.callback_query(F.data.regexp(r"^admin:tickets:(\d+)$"))
async def admin_tickets_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    page = int(re.match(r"^admin:tickets:(\d+)$", cb.data).group(1))
    size = PAGE_SIZE_TICKETS
    rows, total = list_tickets_page(page, size)
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"#{r['id']} | {r['status']}", callback_data=f"adm:tkt:view:{r['id']}:0")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"admin:tickets:{page-1}"))
    if (page + 1) * size < total:
        nav.append(InlineKeyboardButton(text="صفحه بعد", callback_data=f"admin:tickets:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="admin")])
    await cb.message.edit_text("تیکت‌ها:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.regexp(r"^adm:tkt:view:(\d+):(\d+)$"))
async def admin_ticket_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    m = re.match(r"^adm:tkt:view:(\d+):(\d+)$", cb.data)
    tid = int(m.group(1))
    page = int(m.group(2))
    size = 10
    rows, total = list_ticket_messages_page(tid, page, size)
    if not rows:
        text = "پیامی در این صفحه نیست."
    else:
        text_lines = []
        for r in rows:
            who = "کاربر" if r["sender_type"] == "user" else "ادمین"
            text_lines.append(f"[{r['id']}] {who} ({r['sender_id']}): {htmlesc(r.get('content') or r.get('caption') or '')}")
        text = "\n".join(text_lines)
    kb = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"adm:tkt:view:{tid}:{page-1}"))
    if (page + 1) * size < total:
        nav.append(InlineKeyboardButton(text="صفحه بعد", callback_data=f"adm:tkt:view:{tid}:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append(
        [
            InlineKeyboardButton(text="پاسخ", callback_data=f"adm:tkt:reply:{tid}"),
            InlineKeyboardButton(text="بستن", callback_data=f"adm:tkt:close:{tid}"),
        ]
    )
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="admin:tickets:0")])
    await cb.message.edit_text(text or "پیامی در این صفحه نیست.", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.regexp(r"^adm:tkt:close:(\d+)$"))
async def admin_ticket_close(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    tid = int(re.match(r"^adm:tkt:close:(\d+)$", cb.data).group(1))
    ticket_close(tid)
    await cb.answer("تیکت بسته شد.")
    await admin_tickets_list(cb)


@router.callback_query(F.data.regexp(r"^adm:tkt:reply:(\d+)$"))
async def admin_ticket_reply(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    tid = int(re.match(r"^adm:tkt:reply:(\d+)$", cb.data).group(1))
    await state.set_state(AdminReply.waiting)
    await state.update_data(tid=tid)
    await cb.message.edit_text(
        f"پاسخ به تیکت #{tid} را ارسال کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="انصراف", callback_data=f"adm:tkt:view:{tid}:0")]]),
    )


@router.message(StateFilter(AdminReply.waiting))
async def admin_reply_dispatch(m: Message, state: FSMContext):
    data = await state.get_data()
    tid = data.get("tid")
    if not tid:
        await state.clear()
        return
    row = cur.execute("SELECT user_id FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        await m.reply("تیکت پیدا نشد.")
        await state.clear()
        return
    uid = row["user_id"]
    ticket_set_activity(tid)
    try:
        if m.photo:
            sent = await m.bot.send_photo(uid, m.photo[-1].file_id, caption=m.caption or "")
            store_tmsg(tid, "admin", m.from_user.id, "photo", m.photo[-1].file_id, m.caption or "", sent.message_id)
        elif m.document:
            sent = await m.bot.send_document(uid, m.document.file_id, caption=m.caption or "")
            store_tmsg(tid, "admin", m.from_user.id, "document", m.document.file_id, m.caption or "", sent.message_id)
        elif m.voice:
            sent = await m.bot.send_voice(uid, m.voice.file_id, caption=m.caption or "")
            store_tmsg(tid, "admin", m.from_user.id, "voice", m.voice.file_id, m.caption or "", sent.message_id)
        elif m.video:
            sent = await m.bot.send_video(uid, m.video.file_id, caption=m.caption or "")
            store_tmsg(tid, "admin", m.from_user.id, "video", m.video.file_id, m.caption or "", sent.message_id)
        elif m.sticker:
            sent = await m.bot.send_sticker(uid, m.sticker.file_id)
            store_tmsg(tid, "admin", m.from_user.id, "sticker", m.sticker.file_id, None, sent.message_id)
        else:
            sent = await m.bot.send_message(uid, m.text or "")
            store_tmsg(tid, "admin", m.from_user.id, "text", m.text or "", None, sent.message_id)
        await m.reply("پیام ارسال شد.")
    except Exception:
        await m.reply("ارسال پیام به کاربر با خطا مواجه شد.")
    await state.clear()
