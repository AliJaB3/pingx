from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from config import ADMIN_IDS, PAGE_SIZE_TICKETS
from db import (
    get_or_open_ticket, ticket_close, ticket_set_activity, store_tmsg,
    list_tickets_page, list_ticket_messages_page, cur,
)
from utils import htmlesc

router = Router()


class AdminReply(StatesGroup):
    waiting = State()


def kb_ticket_user():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بستن تیکت", callback_data="ticket:close")],
            [InlineKeyboardButton(text="خانه 🏠", callback_data="home")],
        ]
    )


@router.callback_query(F.data == "support")
async def user_support(cb: CallbackQuery):
    try:
        if getattr(cb.message.chat, "type", "private") != "private":
            return
    except Exception:
        pass
    trow = cur.execute(
        "SELECT id,status FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (cb.from_user.id,),
    ).fetchone()
    if trow:
        await cb.message.edit_text(
            f"You already have an open ticket: #{trow['id']}\nPlease continue the conversation here.",
            reply_markup=kb_ticket_user(),
        )
    else:
        tid = get_or_open_ticket(cb.from_user.id)
        for aid in ADMIN_IDS:
            try:
                await cb.bot.send_message(
                    aid,
                    f"New ticket #{tid} from <a href=\"tg://user?id={cb.from_user.id}\">{htmlesc(cb.from_user.full_name or cb.from_user.username or str(cb.from_user.id))}</a>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await cb.message.edit_text(
            f"Ticket opened: #{tid}\nPlease describe your issue.",
            reply_markup=kb_ticket_user(),
        )


@router.callback_query(F.data == "ticket:close")
async def user_ticket_close(cb: CallbackQuery):
    try:
        if getattr(cb.message.chat, "type", "private") != "private":
            return
    except Exception:
        pass
    row = cur.execute(
        "SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (cb.from_user.id,),
    ).fetchone()
    if not row:
        return await cb.answer("No open ticket.", show_alert=True)
    ticket_close(row["id"])
    for aid in ADMIN_IDS:
        try:
            await cb.bot.send_message(aid, f"Ticket #{row['id']} closed by user.")
        except Exception:
            pass
        await cb.message.edit_text(
            "تیکت بسته شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="ایجاد تیکت جدید", callback_data="support")],
                    [InlineKeyboardButton(text="خانه 🏠", callback_data="home")],
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
        f"New message on ticket #{tid} from "
        f"<a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="پاسخ ✍️", callback_data=f"adm:tkt:reply:{tid}"),
                InlineKeyboardButton(text="بستن 🔒", callback_data=f"adm:tkt:close:{tid}"),
            ],
            [InlineKeyboardButton(text="نمایش سابقه", callback_data=f"adm:tkt:view:{tid}:0")],
        ]
    )
    for aid in ADMIN_IDS:
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
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Access denied", show_alert=True)
    import re

    page = int(re.match(r"^admin:tickets:(\d+)$", cb.data).group(1))
    rows, total = list_tickets_page(page, PAGE_SIZE_TICKETS)
    kb = []
    for t in rows:
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"#{t['id']} · {t['status']} · user {t['user_id']}",
                    callback_data=f"adm:tkt:view:{t['id']}:0",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"admin:tickets:{page-1}"))
    if (page + 1) * PAGE_SIZE_TICKETS < total:
        nav.append(InlineKeyboardButton(text="بعدی", callback_data=f"admin:tickets:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    try:
        await cb.message.edit_text("Tickets:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.bot.send_message(cb.from_user.id, "Tickets:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.regexp(r"^adm:tkt:view:(\d+):(\d+)$"))
async def admin_ticket_view(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Access denied", show_alert=True)
    import re

    m = re.match(r"^adm:tkt:view:(\d+):(\d+)$", cb.data)
    tid = int(m.group(1))
    page = int(m.group(2))
    rows, total = list_ticket_messages_page(tid, page, 10)
    txt = [f"Ticket history #{tid} (page {page+1})"]
    for r in rows:
        who = "User" if r["sender_type"] == "user" else "Admin"
        if r["kind"] == "text":
            txt.append(f"{who}: {htmlesc(r['content'] or '')}")
        else:
            txt.append(f"{who}: [{r['kind']}] {htmlesc(r.get('caption') or '')}")
    kb = [
        [
            InlineKeyboardButton(text="پاسخ ✍️", callback_data=f"adm:tkt:reply:{tid}"),
            InlineKeyboardButton(text="بستن 🔒", callback_data=f"adm:tkt:close:{tid}"),
        ]
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"adm:tkt:view:{tid}:{page-1}"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton(text="بعدی", callback_data=f"adm:tkt:view:{tid}:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:tickets:0")])
    try:
        await cb.message.edit_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.bot.send_message(cb.from_user.id, "\n".join(txt), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^adm:tkt:close:(\d+)$"))
async def admin_ticket_close(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Access denied", show_alert=True)
    import re

    tid = int(re.match(r"^adm:tkt:close:(\d+)$", cb.data).group(1))
    row = cur.execute("SELECT user_id,status FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        return await cb.answer("Ticket not found")
    if str(row["status"]) != "open":
        return await cb.answer("Ticket already closed")
    ticket_close(tid)
    try:
        await cb.bot.send_message(row["user_id"], f"Your ticket #{tid} has been closed.")
    except Exception:
        pass
    await admin_tickets_list(cb)


@router.callback_query(F.data.regexp(r"^adm:tkt:reply:(\d+)$"))
async def admin_ticket_reply(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Access denied", show_alert=True)
    import re

    tid = int(re.match(r"^adm:tkt:reply:(\d+)$", cb.data).group(1))
    await state.set_state(AdminReply.waiting)
    await state.update_data(tid=tid)
    prompt = (
        f"Replying to ticket #{tid}. Send your message (text/media).\n"
        f"Send /cancel to abort."
    )
    try:
        await cb.message.edit_text(
            prompt,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data=f"adm:tkt:view:{tid}:0")]]
            ),
        )
    except Exception:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.bot.send_message(
            cb.from_user.id,
            prompt,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data=f"adm:tkt:view:{tid}:0")]]
            ),
        )


@router.message(AdminReply.waiting)
async def admin_reply_dispatch(m: Message, state: FSMContext):
    if m.text and m.text.strip() == "/cancel":
        await state.clear()
        return await m.reply("Cancelled.")
    data = await state.get_data()
    tid = int(data.get("tid"))
    row = cur.execute("SELECT user_id,status FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        await state.clear()
        return await m.reply("Ticket not found.")
    if str(row["status"]) != "open":
        await state.clear()
        return await m.reply("Ticket is closed.")
    uid = row["user_id"]
    try:
        if m.photo:
            msg = await m.bot.send_photo(uid, m.photo[-1].file_id, caption=(m.caption or ""))
            store_tmsg(tid, "admin", m.from_user.id, "photo", m.photo[-1].file_id, m.caption or "", msg.message_id)
        elif m.document:
            msg = await m.bot.send_document(uid, m.document.file_id, caption=(m.caption or ""))
            store_tmsg(tid, "admin", m.from_user.id, "document", m.document.file_id, m.caption or "", msg.message_id)
        elif m.voice:
            msg = await m.bot.send_voice(uid, m.voice.file_id, caption=(m.caption or ""))
            store_tmsg(tid, "admin", m.from_user.id, "voice", m.voice.file_id, m.caption or "", msg.message_id)
        elif m.video:
            msg = await m.bot.send_video(uid, m.video.file_id, caption=(m.caption or ""))
            store_tmsg(tid, "admin", m.from_user.id, "video", m.video.file_id, m.caption or "", msg.message_id)
        else:
            msg = await m.bot.send_message(uid, m.text or "")
            store_tmsg(tid, "admin", m.from_user.id, "text", m.text or "", None, msg.message_id)
    except Exception as e:
        await m.reply(f"Send failed: {e}")
        return
    await state.clear()
    await m.reply("Sent.")
