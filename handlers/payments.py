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
            [InlineKeyboardButton(text="افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="home")],
        ]
    )
    await cb.message.edit_text(f"موجودی شما: <b>{bal:,}</b>", reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "topup")
async def topup_ask_amount(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Topup.amount)
    msg = (
        "<b>افزایش موجودی کیف‌پول</b>\n\n"
        "مبلغ واریزی را ارسال کنید. سپس در صورت تمایل، عکس رسید را ارسال کنید.\n\n"
        f"<b>شماره کارت:</b> <code>{CARD_NUMBER}</code>\n"
        "مثال: 150000\n\n"
        f"حداکثر تعداد تصاویر: {MAX_RECEIPT_PHOTOS} | حداکثر حجم هر تصویر: {MAX_RECEIPT_MB}MB"
    )
    await cb.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="wallet")]]), parse_mode=ParseMode.HTML)


@router.message(Topup.amount)
async def topup_got_amount(m: Message, state: FSMContext):
    try:
        amount = int(str(m.text).replace(",", "").strip())
    except Exception:
        await m.reply("مبلغ نامعتبر است. یک عدد ارسال کنید.")
        return
    await state.update_data(amount=amount, photos=[])
    await state.set_state(Topup.note)
    await m.reply("حالا عکس/عکس‌های رسید را بفرستید، سپس عبارت done را ارسال کنید.")


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
            await m.reply("حجم تصویر زیاد است.")
            return
    except Exception:
        pass
    if len(photos) >= MAX_RECEIPT_PHOTOS:
        await m.reply("حداکثر تعداد تصاویر مجاز است.")
        return
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.reply(f"تصویر ثبت شد ({len(photos)}/{MAX_RECEIPT_PHOTOS}).")


@router.message(Topup.note)
async def topup_collect(m: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    photos = data.get("photos", [])
    if m.text and m.text.strip().lower() == "done":
        pid = db_new_payment(m.from_user.id, amount, data.get("note", ""), photos)
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="نمایش", callback_data=f"payview:{pid}")]])
        for aid in get_admin_ids():
            try:
                if photos:
                    await m.bot.send_message(aid, f"درخواست شارژ #{pid}\nاز <a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\nمبلغ: {amount:,}", parse_mode=ParseMode.HTML)
                    for ph in photos:
                        await m.bot.send_photo(aid, ph, caption=f"شارژ #{pid}")
                    await m.bot.send_message(aid, "اقدام:", reply_markup=kb)
                else:
                    await m.bot.send_message(aid, f"درخواست شارژ #{pid}\nاز <a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\nمبلغ: {amount:,}", reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        await m.reply("درخواست شما ثبت شد. منتظر تأیید ادمین باشید.")
    else:
        await state.update_data(note=m.text or "")
        await m.reply("توضیح ثبت شد. در پایان عبارت done را ارسال کنید.")


@router.callback_query(F.data.regexp(r"^admin:pending:(\d+)$"))
async def admin_pending(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin:pending:(\d+)$", cb.data or "")
    page = int(m.group(1)) if m else 0
    limit = PAGE_SIZE_PAYMENTS
    offset = page * limit
    pend, total = db_list_pending_payments_page(offset, limit)
    if not pend:
        await cb.message.edit_text("درخواستی در انتظار تأیید نیست.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]))
        return
    rows = [[InlineKeyboardButton(text=f"#{p['id']} · {p['amount']:,} · کاربر {p['user_id']}", callback_data=f"payview:{p['id']}")] for p in pend]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"admin:pending:{page-1}"))
    if (page + 1) * limit < total:
        nav.append(InlineKeyboardButton(text="بعدی", callback_data=f"admin:pending:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    await cb.message.edit_text("پرداخت‌های در انتظار:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("payview:"))
async def admin_pay_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    pid = int(cb.data.split(":")[1])
    p = db_get_payment(pid)
    if not p:
        return await cb.answer("یافت نشد")
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (p["user_id"],)).fetchone()
    nm = (" ".join(filter(None, [u and u["first_name"] or "", u and u["last_name"] or ""])) or (u and u["username"] or str(p["user_id"]))).strip()
    user_html = f'<a href="tg://user?id={p["user_id"]}">{htmlesc(nm)}</a>'
    caption = (
        f"<b>درخواست #{p['id']}</b>\nکاربر: {user_html}\nمبلغ: <b>{p['amount']:,}</b>\n"
        f"توضیح: {htmlesc(p['note'] or '-')}\nوضعیت: <b>{p['status']}</b>\nتاریخ: {p['created_at'][:19].replace('T',' ')}"
    )
    photos = json.loads(p.get("photos_json") or "[]")

    def kb(pid: int):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید ✅", callback_data=f"payok:{pid}"),
                    InlineKeyboardButton(text="رد ❌", callback_data=f"payno:{pid}"),
                ],
                [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:pending:0")],
            ]
        )

    if photos:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.bot.send_message(cb.from_user.id, caption, parse_mode=ParseMode.HTML)
        for ph in photos:
            await cb.bot.send_photo(cb.from_user.id, ph, caption=f"درخواست #{p['id']}")
        await cb.bot.send_message(cb.from_user.id, "اقدام:", reply_markup=kb(pid))
    else:
        await cb.message.edit_text(caption, reply_markup=kb(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("payok:"))
async def admin_pay_ok(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    pid = int(cb.data.split(":")[1])
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        return await cb.answer("قبلاً رسیدگی شده")
    db_update_payment_status(pid, "approved")
    db_add_wallet(row["user_id"], row["amount"])
    try:
        await cb.bot.send_message(row["user_id"], f"پرداخت شما {row['amount']:,} تأیید شد.")
    except Exception:
        pass
    await admin_pending(cb)


@router.callback_query(F.data.startswith("payno:"))
async def admin_pay_no(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    pid = int(cb.data.split(":")[1])
    row = db_get_payment(pid)
    if not row or row["status"] != "pending":
        return await cb.answer("قبلاً رسیدگی شده")
    db_update_payment_status(pid, "rejected")
    try:
        await cb.bot.send_message(row["user_id"], f"پرداخت شما {row['amount']:,} رد شد.")
    except Exception:
        pass
    await admin_pending(cb)




