from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import CARD_NUMBER, MAX_RECEIPT_PHOTOS, MAX_RECEIPT_MB, ADMIN_IDS, PAGE_SIZE_PAYMENTS
from db import (
    db_get_wallet, db_new_payment, db_get_payment, db_add_wallet, db_update_payment_status,
    db_list_pending_payments_page, cur,
)
from utils import htmlesc
import json, re

router = Router()


class Topup(StatesGroup):
    amount = State()
    note = State()


@router.callback_query(F.data == "wallet")
async def wallet(cb: CallbackQuery):
    bal = db_get_wallet(cb.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ط§ظپط²ط§غŒط´ ظ…ظˆط¬ظˆط¯غŒ", callback_data="topup")],
            [InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="home")],
        ]
    )
    await cb.message.edit_text(f"ظ…ظˆط¬ظˆط¯غŒ ط´ظ…ط§: <b>{bal:,}</b>", reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "topup")
async def topup_ask_amount(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Topup.amount)
    msg = (
        "<b>ط§ظپط²ط§غŒط´ ظ…ظˆط¬ظˆط¯غŒ ع©غŒظپâ€Œظ¾ظˆظ„</b>\n\n"
        "ظ…ط¨ظ„ط؛ ظˆط§ط±غŒط²غŒ ط±ط§ ط§ط±ط³ط§ظ„ ع©ظ†غŒط¯. ط³ظ¾ط³ ط¯ط± طµظˆط±طھ طھظ…ط§غŒظ„طŒ ط¹ع©ط³ ط±ط³غŒط¯ ط±ط§ ط§ط±ط³ط§ظ„ ع©ظ†غŒط¯.\n\n"
        f"<b>ط´ظ…ط§ط±ظ‡ ع©ط§ط±طھ:</b> <code>{CARD_NUMBER}</code>\n"
        "ظ…ط«ط§ظ„: 150000\n\n"
        f"ط­ط¯ط§ع©ط«ط± طھط¹ط¯ط§ط¯ طھطµط§ظˆغŒط±: {MAX_RECEIPT_PHOTOS} | ط­ط¯ط§ع©ط«ط± ط­ط¬ظ… ظ‡ط± طھطµظˆغŒط±: {MAX_RECEIPT_MB}MB"
    )
    await cb.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="wallet")]]), parse_mode=ParseMode.HTML)


@router.message(Topup.amount)
async def topup_got_amount(m: Message, state: FSMContext):
    try:
        amount = int(str(m.text).replace(",", "").strip())
    except Exception:
        await m.reply("ظ…ط¨ظ„ط؛ ظ†ط§ظ…ط¹طھط¨ط± ط§ط³طھ. غŒع© ط¹ط¯ط¯ ط§ط±ط³ط§ظ„ ع©ظ†غŒط¯.")
        return
    await state.update_data(amount=amount, photos=[])
    await state.set_state(Topup.note)
    await m.reply("ط­ط§ظ„ط§ ط¹ع©ط³/ط¹ع©ط³â€Œظ‡ط§غŒ ط±ط³غŒط¯ ط±ط§ ط¨ظپط±ط³طھغŒط¯طŒ ط³ظ¾ط³ ط¹ط¨ط§ط±طھ done ط±ط§ ط§ط±ط³ط§ظ„ ع©ظ†غŒط¯.")


@router.message(F.photo)
async def collect_photo(m: Message, state: FSMContext):
    s = await state.get_state()
    if not s or "Topup" not in s:
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    try:
        sz = int(m.photo[-1].file_size or 0)
        if sz > int(MAX_RECEIPT_MB) * 1024 * 1024:
            await m.reply("ط­ط¬ظ… طھطµظˆغŒط± ط²غŒط§ط¯ ط§ط³طھ.")
            return
    except Exception:
        pass
    if len(photos) >= MAX_RECEIPT_PHOTOS:
        await m.reply("ط­ط¯ط§ع©ط«ط± طھط¹ط¯ط§ط¯ طھطµط§ظˆغŒط± ظ…ط¬ط§ط² ط§ط³طھ.")
        return
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.reply(f"طھطµظˆغŒط± ط«ط¨طھ ط´ط¯ ({len(photos)}/{MAX_RECEIPT_PHOTOS}).")


@router.message(Topup.note)
async def topup_collect(m: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    photos = data.get("photos", [])
    if m.text and m.text.strip().lower() == "done":
        pid = db_new_payment(m.from_user.id, amount, data.get("note", ""), photos)
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ظ†ظ…ط§غŒط´", callback_data=f"payview:{pid}")]])
        for aid in get_admin_ids():
            try:
                if photos:
                    await m.bot.send_message(aid, f"ط¯ط±ط®ظˆط§ط³طھ ط´ط§ط±عک #{pid}\nط§ط² <a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\nظ…ط¨ظ„ط؛: {amount:,}", parse_mode=ParseMode.HTML)
                    for ph in photos:
                        await m.bot.send_photo(aid, ph, caption=f"ط´ط§ط±عک #{pid}")
                    await m.bot.send_message(aid, "ط§ظ‚ط¯ط§ظ…:", reply_markup=kb)
                else:
                    await m.bot.send_message(aid, f"ط¯ط±ط®ظˆط§ط³طھ ط´ط§ط±عک #{pid}\nط§ط² <a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\nظ…ط¨ظ„ط؛: {amount:,}", reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        await m.reply("ط¯ط±ط®ظˆط§ط³طھ ط´ظ…ط§ ط«ط¨طھ ط´ط¯. ظ…ظ†طھط¸ط± طھط£غŒغŒط¯ ط§ط¯ظ…غŒظ† ط¨ط§ط´غŒط¯.")
    else:
        await state.update_data(note=m.text or "")
        await m.reply("طھظˆط¶غŒط­ ط«ط¨طھ ط´ط¯. ط¯ط± ظ¾ط§غŒط§ظ† ط¹ط¨ط§ط±طھ done ط±ط§ ط§ط±ط³ط§ظ„ ع©ظ†غŒط¯.")


@router.callback_query(F.data.regexp(r"^admin:pending:(\d+)$"))
async def admin_pending(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("ط¯ط³طھط±ط³غŒ ط؛غŒط±ظ…ط¬ط§ط²", show_alert=True)
    m = re.match(r"^admin:pending:(\d+)$", cb.data or "")
    page = int(m.group(1)) if m else 0
    limit = PAGE_SIZE_PAYMENTS
    offset = page * limit
    pend, total = db_list_pending_payments_page(offset, limit)
    if not pend:
        await cb.message.edit_text("ط¯ط±ط®ظˆط§ط³طھغŒ ط¯ط± ط§ظ†طھط¸ط§ط± طھط£غŒغŒط¯ ظ†غŒط³طھ.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="admin")]]))
        return
    rows = [[InlineKeyboardButton(text=f"#{p['id']} آ· {p['amount']:,} آ· ع©ط§ط±ط¨ط± {p['user_id']}", callback_data=f"payview:{p['id']}")] for p in pend]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="ظ‚ط¨ظ„غŒ", callback_data=f"admin:pending:{page-1}"))
    if (page + 1) * limit < total:
        nav.append(InlineKeyboardButton(text="ط¨ط¹ط¯غŒ", callback_data=f"admin:pending:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="admin")])
    await cb.message.edit_text("ظ¾ط±ط¯ط§ط®طھâ€Œظ‡ط§غŒ ط¯ط± ط§ظ†طھط¸ط§ط±:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("payview:"))
async def admin_pay_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("ط¯ط³طھط±ط³غŒ ط؛غŒط±ظ…ط¬ط§ط²", show_alert=True)
    pid = int(cb.data.split(":")[1])
    p = db_get_payment(pid)
    if not p:
        return await cb.answer("غŒط§ظپطھ ظ†ط´ط¯")
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (p["user_id"],)).fetchone()
    nm = (" ".join(filter(None, [u and u["first_name"] or "", u and u["last_name"] or ""])) or (u and u["username"] or str(p["user_id"]))).strip()
    user_html = f'<a href="tg://user?id={p["user_id"]}">{htmlesc(nm)}</a>'
    caption = (
        f"<b>ط¯ط±ط®ظˆط§ط³طھ #{p['id']}</b>\nع©ط§ط±ط¨ط±: {user_html}\nظ…ط¨ظ„ط؛: <b>{p['amount']:,}</b>\n"
        f"طھظˆط¶غŒط­: {htmlesc(p['note'] or '-')}\nظˆط¶ط¹غŒطھ: <b>{p['status']}</b>\nطھط§ط±غŒط®: {p['created_at'][:19].replace('T',' ')}"
    )
    photos = json.loads(p.get("photos_json") or "[]")

    def kb(pid: int):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="طھط£غŒغŒط¯ âœ…", callback_data=f"payok:{pid}"),
                    InlineKeyboardButton(text="ط±ط¯ â‌Œ", callback_data=f"payno:{pid}"),
                ],
                [InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="admin:pending:0")],
            ]
        )

    if photos:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.bot.send_message(cb.from_user.id, caption, parse_mode=ParseMode.HTML)
        for ph in photos:
            await cb.bot.send_photo(cb.from_user.id, ph, caption=f"ط¯ط±ط®ظˆط§ط³طھ #{p['id']}")
        await cb.bot.send_message(cb.from_user.id, "ط§ظ‚ط¯ط§ظ…:", reply_markup=kb(pid))
    else:
        await cb.message.edit_text(caption, reply_markup=kb(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("payok:"))
async def admin_pay_ok(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("ط¯ط³طھط±ط³غŒ ط؛غŒط±ظ…ط¬ط§ط²", show_alert=True)
    pid = int(cb.data.split(":")[1])
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        return await cb.answer("ظ‚ط¨ظ„ط§ظ‹ ط±ط³غŒط¯ع¯غŒ ط´ط¯ظ‡")
    db_update_payment_status(pid, "approved")
    db_add_wallet(row["user_id"], row["amount"])
    try:
        await cb.bot.send_message(row["user_id"], f"ظ¾ط±ط¯ط§ط®طھ ط´ظ…ط§ {row['amount']:,} طھط£غŒغŒط¯ ط´ط¯.")
    except Exception:
        pass
    await admin_pending(cb)


@router.callback_query(F.data.startswith("payno:"))
async def admin_pay_no(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("ط¯ط³طھط±ط³غŒ ط؛غŒط±ظ…ط¬ط§ط²", show_alert=True)
    pid = int(cb.data.split(":")[1])
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        return await cb.answer("ظ‚ط¨ظ„ط§ظ‹ ط±ط³غŒط¯ع¯غŒ ط´ط¯ظ‡")
    db_update_payment_status(pid, "rejected")
    try:
        await cb.bot.send_message(row["user_id"], f"ظ¾ط±ط¯ط§ط®طھ ط´ظ…ط§ {row['amount']:,} ط±ط¯ ط´ط¯.")
    except Exception:
        pass
    await admin_pending(cb)



