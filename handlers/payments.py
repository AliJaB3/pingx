import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from config import CARD_NUMBER, MAX_RECEIPT_PHOTOS, MAX_RECEIPT_MB, PAGE_SIZE_PAYMENTS
from db import (
    is_admin,
    get_admin_ids,
    db_get_wallet,
    db_new_payment,
    db_get_payment,
    db_add_wallet,
    db_update_payment_status,
    db_list_pending_payments_page,
    get_setting,
)
from utils import htmlesc
import json, re

router = Router()
logger = logging.getLogger("pingx.payments")


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
    note = State()


def _kb_amounts():
    amounts = [150_000, 300_000, 500_000, 1_000_000, 2_000_000]
    rows = [[InlineKeyboardButton(text=f"{amt:,} تومان", callback_data=f"topamt:{amt}")] for amt in amounts]
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="wallet")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_receipt_flow():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ ثبت و ارسال برای ادمین", callback_data="topdone")],
            [InlineKeyboardButton(text="↩️ انصراف", callback_data="wallet")],
        ]
    )


def _kb_amount_selected():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📎 ارسال رسید", callback_data="nosend")],
            [InlineKeyboardButton(text="✅ ثبت و ارسال برای ادمین", callback_data="topdone")],
            [InlineKeyboardButton(text="↩️ تغییر مبلغ", callback_data="topup")],
        ]
    )


@router.callback_query(F.data == "wallet")
async def wallet(cb: CallbackQuery):
    bal = db_get_wallet(cb.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="↩️ بازگشت", callback_data="home")],
        ]
    )
    await cb.message.edit_text(f"💰 موجودی کیف پول: <b>{bal:,}</b> تومان", reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "topup")
async def topup_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Topup.note)
    card_number = _runtime_card_number()
    max_photos = _runtime_max_photos()
    max_mb = _runtime_max_mb()
    msg = (
        "<b>🔼 افزایش موجودی</b>\n"
        "یک مبلغ را از دکمه‌ها انتخاب کن.\n"
        "بعد رسید یا توضیحات را بفرست و در پایان \"ثبت\" را بزن.\n\n"
        f"💳 کارت مقصد: <code>{card_number}</code>\n"
        f"🖼 حداکثر عکس: {max_photos} | 📏 حداکثر حجم هر عکس: {max_mb}MB"
    )
    await cb.message.edit_text(msg, reply_markup=_kb_amounts(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("topamt:"))
async def topup_select_amount(cb: CallbackQuery, state: FSMContext):
    try:
        amount = int(cb.data.split(":")[1])
    except Exception:
        return await cb.answer("مبلغ نامعتبر", show_alert=True)
    await state.update_data(amount=amount, photos=[], notes=[])
    logger.info("Topup amount set uid=%s amount=%s", cb.from_user.id, amount)
    await cb.message.edit_text(
        f"مبلغ انتخاب شد: <b>{amount:,}</b> تومان\n"
        "رسید/توضیحات را بفرست و در پایان «ثبت و ارسال برای ادمین» را بزن.",
        reply_markup=_kb_amount_selected(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "nosend")
async def topup_prompt_receipt(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("amount") is None:
        return await cb.answer("ابتدا مبلغ شارژ را انتخاب کن.", show_alert=True)
    max_photos = _runtime_max_photos()
    max_mb = _runtime_max_mb()
    msg = (
        "رسید/توضیحات را بفرست و در پایان «ثبت و ارسال برای ادمین» را بزن.\n"
        f"📎 می‌توانی حداکثر {max_photos} عکس تا {max_mb}MB برای هر عکس بفرستی."
    )
    await cb.message.answer(msg, reply_markup=_kb_receipt_flow())
    await cb.answer()


@router.message(StateFilter(Topup.note), F.photo)
async def collect_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("amount") is None:
        return
    photos = data.get("photos", [])
    max_mb = _runtime_max_mb()
    try:
        sz = int(m.photo[-1].file_size or 0)
        if sz > int(max_mb) * 1024 * 1024:
            await m.reply("⚠️ حجم این عکس از حد مجاز بیشتر است.", reply_markup=_kb_receipt_flow())
            return
    except Exception:
        pass
    max_photos = _runtime_max_photos()
    if len(photos) >= max_photos:
        await m.reply("⚠️ تعداد عکس بیش از حد مجاز است.", reply_markup=_kb_receipt_flow())
        return
    photos.append(m.photo[-1].file_id)
    await state.update_data(photos=photos)
    await m.reply(f"🖼 عکس ذخیره شد ({len(photos)}/{max_photos}).", reply_markup=_kb_receipt_flow())


@router.message(StateFilter(Topup.note))
async def topup_collect(m: Message, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("amount") is None:
        return
    amount = data.get("amount")
    photos = data.get("photos", [])
    notes = data.get("notes", [])
    note = m.html_text or m.text or ""
    if note:
        notes.append(note)
        await state.update_data(notes=notes)
        await m.reply("📝 توضیح ثبت شد. در پایان روی «ثبت و ارسال برای ادمین» بزن.", reply_markup=_kb_receipt_flow())


@router.callback_query(F.data == "topdone")
async def topup_finalize(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("amount") is None:
        return await cb.answer("مبلغ انتخاب نشده است.", show_alert=True)
    amount = data.get("amount")
    photos = data.get("photos", [])
    notes = data.get("notes", [])
    note_txt = "\n".join(n for n in notes if n).strip()
    pid = db_new_payment(cb.from_user.id, amount, note_txt, photos)
    logger.info("Topup submitted uid=%s pid=%s amount=%s photos=%s note_len=%s", cb.from_user.id, pid, amount, len(photos), len(note_txt))
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="مشاهده رسید", callback_data=f"payview:{pid}")]])
    for aid in get_admin_ids():
        try:
            base_msg = (
                f"پرداخت جدید #{pid}\n"
                f"از <a href=\"tg://user?id={cb.from_user.id}\">{htmlesc(cb.from_user.full_name or cb.from_user.username or str(cb.from_user.id))}</a>\n"
                f"مبلغ: {amount:,}"
            )
            if note_txt:
                base_msg += f"\nتوضیحات: {htmlesc(note_txt)}"
            await cb.bot.send_message(aid, base_msg, parse_mode=ParseMode.HTML)
            for ph in photos:
                await cb.bot.send_photo(aid, ph, caption=f"رسید #{pid}")
            await cb.bot.send_message(aid, "اقدام:", reply_markup=kb)
        except Exception:
            continue
    await cb.message.edit_text("درخواست شما ثبت شد و پس از بررسی ادمین نتیجه اعلام می‌شود.", reply_markup=_topup_main_keyboard(), parse_mode=ParseMode.HTML)


def _wallet_text(bal: int) -> str:
    return f"💰 موجودی فعلی شما: <b>{bal:,}</b> تومان"


def _topup_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="home")],
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
        kb_rows.append([InlineKeyboardButton(text=f"#{r['id']} مبلغ {r['amount']:,}", callback_data=f"payview:{r['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"admin:pending:{page-1}"))
    if off + size < total:
        nav.append(InlineKeyboardButton(text="صفحه بعد ➡️", callback_data=f"admin:pending:{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="admin")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await cb.message.edit_text("پرداخت‌های در انتظار بررسی:", reply_markup=kb)


def kb(pid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ تایید و شارژ", callback_data=f"payok:{pid}")],
            [InlineKeyboardButton(text="✖️ رد پرداخت", callback_data=f"payno:{pid}")],
            [InlineKeyboardButton(text="↩️ بازگشت", callback_data="admin:pending:0")],
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
    logger.info("Topup approved pid=%s uid=%s amount=%s by_admin=%s", pid, r["user_id"], r["amount"], cb.from_user.id)
    try:
        await cb.bot.send_message(r["user_id"], f"پرداخت شما به مبلغ {r['amount']:,} تایید شد و به کیف پولتان اضافه شد.")
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
