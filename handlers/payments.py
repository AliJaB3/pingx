from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from config import CARD_NUMBER, MAX_RECEIPT_PHOTOS, MAX_RECEIPT_MB, PAGE_SIZE_PAYMENTS
from db import (
    is_admin, get_admin_ids,
    db_get_wallet, db_new_payment, db_get_payment, db_add_wallet, db_update_payment_status,
    db_list_pending_payments_page, cur, get_setting,
)
from utils import htmlesc
import json, re

router = Router()


def _runtime_card_number() -> str:
    return (get_setting("CARD_NUMBER", CARD_NUMBER) or CARD_NUMBER).strip()


def _runtime_max_photos() -> int:
    try:
        val = int(str(get_setting("MAX_RECEIPT_PHOTOS", str(MAX_RECEIPT_PHOTOS))).strip())
        return val if val > 0 else MAX_RECEIPT_PHOTOS
    except Exception:
        return MAX_RECEIPT_PHOTOS


def _runtime_max_mb() -> int:
    try:
        val = int(str(get_setting("MAX_RECEIPT_MB", str(MAX_RECEIPT_MB))).strip())
        return val if val > 0 else MAX_RECEIPT_MB
    except Exception:
        return MAX_RECEIPT_MB


class Topup(StatesGroup):
    amount = State()
    note = State()


@router.callback_query(F.data == "wallet")
async def wallet(cb: CallbackQuery):
    bal = db_get_wallet(cb.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="افزایش موجودی 💳", callback_data="topup")],
            [InlineKeyboardButton(text="بازگشت ↩️", callback_data="home")],
        ]
    )
    await cb.message.edit_text(
        f"موجودی کیف پول: <b>{bal:,}</b> تومان",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "topup")
async def topup_ask_amount(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Topup.amount)
    card_number = _runtime_card_number()
    max_photos = _runtime_max_photos()
    max_mb = _runtime_max_mb()
    msg = (
        "<b>افزایش موجودی 💳</b>\n\n"
        "۱) مبلغ مورد نظر خود را (به تومان) ارسال کنید.\n"
        "۲) رسید یا توضیحات را بفرستید و در پایان عبارت <code>done</code> را تایپ کنید.\n\n"
        f"<b>شماره کارت:</b> <code>{card_number}</code>\n"
        "حداقل پیشنهادی: 150,000 تومان\n"
        f"حداکثر تعداد تصاویر: {max_photos}\n"
        f"حداکثر حجم هر تصویر: {max_mb}MB"
    )
    await cb.message.edit_text(
        msg,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بازگشت ↩️", callback_data="wallet")]]
        ),
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(Topup.amount))
async def topup_got_amount(m: Message, state: FSMContext):
    try:
        amount = int(str(m.text).replace(",", "").strip())
    except Exception:
        await m.reply("مبلغ وارد شده صحیح نیست. لطفاً فقط عدد ارسال کنید.")
        return
    await state.update_data(amount=amount, photos=[], notes=[])
    await state.set_state(Topup.note)
    await m.reply("مبلغ ثبت شد. حالا رسید/توضیح را بفرستید و در پایان عبارت done را ارسال کنید.")


@router.message(StateFilter(Topup.note), F.photo)
async def collect_photo(m: Message, state: FSMContext):
    s = await state.get_state()
    if not s or "Topup" not in s:
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    max_mb = _runtime_max_mb()
    try:
        sz = int(m.photo[-1].file_size or 0)
        if sz > int(max_mb) * 1024 * 1024:
            await m.reply("حجم عکس بیشتر از حد مجاز است.")
            return
    except Exception:
        pass
    max_photos = _runtime_max_photos()
    if len(photos) >= max_photos:
        await m.reply("به حداکثر تعداد عکس مجاز رسیده‌اید.")
        return
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.reply(f"عکس ثبت شد ✅ ({len(photos)}/{max_photos}).")


@router.message(StateFilter(Topup.note))
async def topup_collect(m: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    photos = data.get("photos", [])
    notes = data.get("notes", [])
    if m.text and m.text.strip().lower() == "done":
        note_txt = "\n".join(n for n in notes if n).strip()
        pid = db_new_payment(m.from_user.id, amount, note_txt, photos)
        await state.clear()
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="نمایش درخواست", callback_data=f"payview:{pid}")]]
        )
        for aid in get_admin_ids():
            try:
                base_msg = (
                    f"درخواست شارژ #{pid}\n"
                    f"از <a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\n"
                    f"مبلغ: {amount:,}"
                )
                if note_txt:
                    base_msg += f"\nتوضیح: {htmlesc(note_txt)}"
                await m.bot.send_message(aid, base_msg, parse_mode=ParseMode.HTML)
                for ph in photos:
                    await m.bot.send_photo(aid, ph, caption=f"رسید #{pid}")
                await m.bot.send_message(aid, "اقدام:", reply_markup=kb)
            except Exception:
                continue
        await m.reply("درخواست شما ثبت شد و پس از تایید ادمین، کیف پول شارژ می‌شود.")
        await m.answer(
            _wallet_text(db_get_wallet(m.from_user.id)),
            reply_markup=_topup_main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        note = m.html_text or m.text or ""
        if note:
            notes.append(note)
            await state.update_data(notes=notes)
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
        await cb.message.edit_text(
            "درخواستی در انتظار تأیید نیست.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت ↩️", callback_data="admin")]]),
        )
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"#{p['id']} • {p['amount']:,} تومان • کاربر {p['user_id']}",
                callback_data=f"payview:{p['id']}",
            )
        ]
        for p in pend
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"admin:pending:{page-1}"))
    if (page + 1) * limit < total:
        nav.append(InlineKeyboardButton(text="بعدی", callback_data=f"admin:pending:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="بازگشت ↩️", callback_data="admin")])
    await cb.message.edit_text("پرداخت‌های در انتظار بررسی:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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
        f"<b>درخواست #{p['id']}</b>\n"
        f"کاربر: {user_html}\n"
        f"مبلغ: <b>{p['amount']:,} تومان</b>\n"
        f"توضیح: {htmlesc(p['note'] or '-')}\n"
        f"وضعیت: <b>{p['status']}</b>\n"
        f"تاریخ: {p['created_at'][:19].replace('T',' ')}"
    )
    photos = json.loads(p.get("photos_json") or "[]")

    def kb(pid: int):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید ✅", callback_data=f"payok:{pid}"),
                    InlineKeyboardButton(text="رد ❌", callback_data=f"payno:{pid}"),
                ],
                [InlineKeyboardButton(text="بازگشت ↩️", callback_data="admin:pending:0")],
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
        await cb.bot.send_message(
            row["user_id"],
            f"پرداخت {row['amount']:,} تومان تایید شد و کیف پول شما شارژ گردید.",
        )
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
        await cb.bot.send_message(row["user_id"], f"پرداخت {row['amount']:,} تومان رد شد.")
    except Exception:
        pass
    await admin_pending(cb)




