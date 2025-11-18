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
            [InlineKeyboardButton(text="افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="بازگشت", callback_data="home")],
        ]
    )
    await cb.message.edit_text(
        f"موجودی کیف پول شما: <b>{bal:,}</b> تومان",
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
        "<b>افزایش موجودی</b>\n\n"
        "۱) مبلغ موردنظر را به کارت زیر واریز کنید.\n"
        "۲) رسید یا توضیحات را ارسال کنید و در پایان عبارت <code>done</code> را بفرستید.\n\n"
        f"<b>کارت مقصد:</b> <code>{card_number}</code>\n"
        "حداقل مبلغ پیشنهادی: 150,000 تومان\n"
        f"حداکثر تعداد عکس: {max_photos}\n"
        f"حداکثر حجم هر عکس: {max_mb}MB"
    )
    await cb.message.edit_text(
        msg,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data="wallet")]]
        ),
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(Topup.amount))
async def topup_got_amount(m: Message, state: FSMContext):
    try:
        amount = int(str(m.text).replace(",", "").strip())
    except Exception:
        await m.reply("عدد واردشده نامعتبر است. لطفاً یک مبلغ صحیح بفرستید.")
        return
    await state.update_data(amount=amount, photos=[], notes=[])
    await state.set_state(Topup.note)
    await m.reply("مبلغ ثبت شد. رسید/توضیحات را بفرستید و در پایان <code>done</code> بزنید.")


@router.message(StateFilter(Topup.note), F.photo)
async def collect_photo(m: Message, state: FSMContext):
    s = await state.get_state()
    if not s:
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    max_mb = _runtime_max_mb()
    try:
        sz = int(m.photo[-1].file_size or 0)
        if sz > int(max_mb) * 1024 * 1024:
            await m.reply("حجم این عکس از حد مجاز بیشتر است.")
            return
    except Exception:
        pass
    max_photos = _runtime_max_photos()
    if len(photos) >= max_photos:
        await m.reply("بیش از حد مجاز عکس فرستاده‌اید.")
        return
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.reply(f"عکس ثبت شد ({len(photos)}/{max_photos}).")


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
            inline_keyboard=[[InlineKeyboardButton(text="مشاهده رسید", callback_data=f"payview:{pid}")]]
        )
        for aid in get_admin_ids():
            try:
                base_msg = (
                    f"پرداخت جدید #{pid}\n"
                    f"از <a href=\"tg://user?id={m.from_user.id}\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\n"
                    f"مبلغ: {amount:,}"
                )
                if note_txt:
                    base_msg += f"\nتوضیحات: {htmlesc(note_txt)}"
                await m.bot.send_message(aid, base_msg, parse_mode=ParseMode.HTML)
                for ph in photos:
                    await m.bot.send_photo(aid, ph, caption=f"رسید #{pid}")
                await m.bot.send_message(aid, "اقدام:", reply_markup=kb)
            except Exception:
                continue
        await m.reply("درخواست شما ثبت شد و پس از بررسی ادمین نتیجه اعلام می‌شود.")
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
            await m.reply("توضیح ثبت شد. در پایان <code>done</code> را بفرستید.")


def _wallet_text(bal: int) -> str:
    return f"موجودی فعلی شما: <b>{bal:,}</b> تومان"


def _topup_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="منوی اصلی", callback_data="home")],
        ]
    )


@router.callback_query(F.data.regexp(r"^admin:pending:(\d+)$"))
async def admin_pending(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    page = int(re.match(r"^admin:pending:(\d+)$", cb.data).group(1))
    size = PAGE_SIZE_PAYMENTS
    off = page * size
    rows, total = db_list_pending_payments_page(off, size)
    kb_rows = []
    for r in rows:
        kb_rows.append(
            [InlineKeyboardButton(text=f"#{r['id']} مبلغ {r['amount']:,}", callback_data=f"payview:{r['id']}")]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"admin:pending:{page-1}"))
    if off + size < total:
        nav.append(InlineKeyboardButton(text="صفحه بعد", callback_data=f"admin:pending:{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="بازگشت", callback_data="admin")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await cb.message.edit_text("پرداخت‌های در انتظار بررسی:", reply_markup=kb)


def kb(pid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="تایید و شارژ", callback_data=f"payok:{pid}")],
            [InlineKeyboardButton(text="رد پرداخت", callback_data=f"payno:{pid}")],
            [InlineKeyboardButton(text="بازگشت", callback_data="admin:pending:0")],
        ]
    )


@router.callback_query(F.data.regexp(r"^payview:(\d+)$"))
async def admin_pay_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    pid = int(re.match(r"^payview:(\d+)$", cb.data).group(1))
    r = db_get_payment(pid)
    if not r:
        return await cb.answer("درخواست پیدا نشد", show_alert=True)
    text = (
        f"درخواست #{pid}\n"
        f"کاربر: <a href=\"tg://user?id={r['user_id']}\">{r['user_id']}</a>\n"
        f"مبلغ: {r['amount']:,}\n"
    )
    if r.get("note"):
        text += f"توضیحات:\n{htmlesc(r['note'])}\n"
    try:
        photos = json.loads(r.get("photos_json") or "[]")
    except Exception:
        photos = []
    if photos:
        try:
            await cb.message.delete()
        except Exception:
            pass
        for i, ph in enumerate(photos):
            caption = text if i == 0 else None
            await cb.bot.send_photo(cb.from_user.id, ph, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb(pid) if i == 0 else None)
    else:
        await cb.message.edit_text(text, reply_markup=kb(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^payok:(\d+)$"))
async def admin_pay_ok(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    pid = int(re.match(r"^payok:(\d+)$", cb.data).group(1))
    r = db_get_payment(pid)
    if not r:
        return await cb.answer("درخواست پیدا نشد", show_alert=True)
    db_add_wallet(r["user_id"], r["amount"])
    db_update_payment_status(pid, "approved")
    try:
        await cb.bot.send_message(
            r["user_id"],
            f"پرداخت شما به مبلغ {r['amount']:,} تایید شد و به کیف پولتان اضافه شد.",
        )
    except Exception:
        pass
    await cb.answer("پرداخت تایید شد.")
    await admin_pending(cb)


@router.callback_query(F.data.regexp(r"^payno:(\d+)$"))
async def admin_pay_no(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    pid = int(re.match(r"^payno:(\d+)$", cb.data).group(1))
    r = db_get_payment(pid)
    if not r:
        return await cb.answer("درخواست پیدا نشد", show_alert=True)
    db_update_payment_status(pid, "rejected")
    try:
        await cb.bot.send_message(r["user_id"], "پرداخت شما تایید نشد. لطفاً مجدد ارسال کنید یا با پشتیبانی در تماس باشید.")
    except Exception:
        pass
    await cb.answer("پرداخت رد شد.")
    await admin_pending(cb)
