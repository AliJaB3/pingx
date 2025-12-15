import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from config import CARD_NUMBER, MAX_RECEIPT_PHOTOS, MAX_RECEIPT_MB, PAGE_SIZE_PAYMENTS, SUPPORT_GROUP_ID, TICKET_GROUP_ID
from db import (
    is_admin,
    is_staff,
    is_support,
    get_admin_ids,
    get_support_ids,
    db_get_wallet,
    db_new_payment,
    db_get_payment,
    db_add_wallet,
    db_update_payment_status,
    db_list_pending_payments_page,
    get_setting,
)
from utils import htmlesc, format_toman
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


def _card_note() -> str:
    card = _runtime_card_number()
    return f"\n\n💳 شماره کارت: <code>{htmlesc(card)}</code>" if card else ""


def _with_card_info(text: str) -> str:
    return (text or "") + _card_note()


class Topup(StatesGroup):
    amount = State()
    note = State()


def _kb_amounts():
    amounts = [150_000, 300_000, 500_000, 1_000_000, 2_000_000]
    rows = [[InlineKeyboardButton(text=format_toman(amt), callback_data=f"topamt:{amt}")] for amt in amounts]
    rows.append([InlineKeyboardButton(text="💵 مبلغ دلخواه", callback_data="topamt:custom")])
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="wallet")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_custom_amount():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ بازگشت", callback_data="topup")],
        ]
    )


def _kb_receipt_flow():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ انصراف", callback_data="wallet")],
        ]
    )


def _kb_amount_selected():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ تغییر مبلغ", callback_data="topup")],
            [InlineKeyboardButton(text="❌ لغو و بازگشت", callback_data="wallet")],
        ]
    )


def _normalize_media(items):
    normalized = []
    for entry in items:
        if isinstance(entry, str):
            normalized.append({"kind": "photo", "file_id": entry})
        elif isinstance(entry, dict):
            kind = (entry.get("kind") or "photo").lower()
            fid = entry.get("file_id")
            if fid:
                normalized.append({"kind": kind, "file_id": fid})
    return normalized


async def _send_media(bot, chat_id, media, *, caption=None, parse_mode=None, reply_markup=None):
    kind = (media.get("kind") or "photo").lower()
    fid = media.get("file_id")
    if not fid:
        return None
    if kind == "document":
        return await bot.send_document(chat_id, fid, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
    return await bot.send_photo(chat_id, fid, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)


async def _submit_topup_request(bot, user, amount, media, notes):
    note_txt = "\n".join(n for n in notes if n).strip()
    media = _normalize_media(media)
    pid = db_new_payment(user.id, amount, note_txt, media)
    logger.info(
        "Topup submitted uid=%s pid=%s amount=%s photos=%s note_len=%s",
        user.id,
        pid,
        amount,
        len(media),
        len(note_txt),
    )
    action_kb = _kb_payment_actions(pid, include_back=False)
    summary = (
        f"رسید واریز #{pid}\n"
        f"کاربر: <a href=\"tg://user?id={user.id}\">{htmlesc(user.full_name or user.username or str(user.id))}</a>\n"
        f"آیدی: {user.id}\n"
        f"مبلغ: {format_toman(amount)}"
    )
    if note_txt:
        summary += f"\nتوضیحات: {htmlesc(note_txt)}"

    recipients = set(get_admin_ids()).union(get_support_ids())
    target_group = SUPPORT_GROUP_ID or TICKET_GROUP_ID or None

    async def _send_to(target_id):
        try:
            if media:
                for idx, item in enumerate(media):
                    caption = summary if idx == 0 else None
                    markup = action_kb if idx == 0 else None
                    await _send_media(bot, target_id, item, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
            else:
                await bot.send_message(target_id, summary, parse_mode=ParseMode.HTML, reply_markup=action_kb)
        except Exception:
            logger.warning("Send receipt notify failed chat=%s pid=%s", target_id, pid, exc_info=True)

    for rid in recipients:
        await _send_to(rid)
    if target_group:
        await _send_to(target_group)
    return pid, note_txt


@router.callback_query(F.data == "wallet")
async def wallet(cb: CallbackQuery):
    bal = db_get_wallet(cb.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="↩️ بازگشت", callback_data="home")],
        ]
    )
    await cb.message.edit_text(f"💰 موجودی کیف پول: <b>{format_toman(bal)}</b>", reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "topup")
async def topup_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Topup.amount)
    max_photos = _runtime_max_photos()
    max_mb = _runtime_max_mb()
    msg = (
        "<b>🔼 افزایش موجودی</b>\n"
        "یک مبلغ را از دکمه‌ها انتخاب کن.\n"
        "بعد رسید یا توضیحات را بفرست؛ به محض دریافت اولین عکس، درخواست به‌صورت خودکار ثبت می‌شود.\n\n"
        f"🖼 حداکثر عکس: {max_photos} | 📏 حداکثر حجم هر عکس: {max_mb}MB"
    )
    await cb.message.edit_text(_with_card_info(msg), reply_markup=_kb_amounts(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("topamt:"))
async def topup_select_amount(cb: CallbackQuery, state: FSMContext):
    if cb.data == "topamt:custom":
        await state.set_state(Topup.amount)
        await cb.message.edit_text(
            _with_card_info("مبلغ موردنظر خود را به تومان با عدد ارسال کن."),
            reply_markup=_kb_custom_amount(),
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        amount = int(cb.data.split(":")[1])
    except Exception:
        return await cb.answer("مبلغ نامعتبر", show_alert=True)
    await state.update_data(amount=amount, media=[], notes=[])
    await state.set_state(Topup.note)
    logger.info("Topup amount set uid=%s amount=%s", cb.from_user.id, amount)
    await cb.message.edit_text(
        _with_card_info(
            f"مبلغ انتخاب شد: <b>{format_toman(amount)}</b>\nرسید/توضیحات را بفرست؛ پس از دریافت عکس، درخواست به‌صورت خودکار ثبت می‌شود."
        ),
        reply_markup=_kb_amount_selected(),
        parse_mode=ParseMode.HTML,
    )


async def _handle_receipt_upload(
    m: Message,
    state: FSMContext,
    *,
    file_id: str,
    file_kind: str,
    file_size: int,
    caption_text: str,
):
    data = await state.get_data()
    if not data or data.get("amount") is None:
        return
    amount = data.get("amount")
    notes = list(data.get("notes", []))
    media = list(data.get("media", []))
    max_mb = _runtime_max_mb()
    if file_size and file_size > int(max_mb) * 1024 * 1024:
        await m.reply(_with_card_info("⚠️ حجم این فایل از حد مجاز بیشتر است."), reply_markup=_kb_receipt_flow(), parse_mode=ParseMode.HTML)
        return
    max_photos = _runtime_max_photos()
    if len(media) >= max_photos:
        await m.reply(_with_card_info("⚠️ تعداد فایل بیش از حد مجاز است."), reply_markup=_kb_receipt_flow(), parse_mode=ParseMode.HTML)
        return
    media.append({"kind": file_kind, "file_id": file_id})
    if caption_text:
        notes.append(caption_text)
    await state.clear()
    pid, _ = await _submit_topup_request(m.bot, m.from_user, amount, media, notes)
    await m.reply(
        _with_card_info(f"✅ رسید دریافت شد و با شماره #{pid} برای بررسی ارسال شد."),
        reply_markup=_topup_main_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(Topup.note), F.photo)
async def collect_photo(m: Message, state: FSMContext):
    sz = 0
    try:
        sz = int(m.photo[-1].file_size or 0)
    except Exception:
        sz = 0
    await _handle_receipt_upload(
        m,
        state,
        file_id=m.photo[-1].file_id,
        file_kind="photo",
        file_size=sz,
        caption_text=m.caption or "",
    )


@router.message(StateFilter(Topup.note), F.document)
async def collect_document(m: Message, state: FSMContext):
    sz = 0
    try:
        sz = int(m.document.file_size or 0)
    except Exception:
        sz = 0
    await _handle_receipt_upload(
        m,
        state,
        file_id=m.document.file_id,
        file_kind="document",
        file_size=sz,
        caption_text=m.caption or "",
    )


@router.message(StateFilter(Topup.note))
async def topup_collect(m: Message, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("amount") is None:
        return
    notes = data.get("notes", [])
    note = m.html_text or m.text or ""
    if note:
        notes.append(note)
        await state.update_data(notes=notes)
        await m.reply(
            _with_card_info("📝 توضیح ثبت شد. رسید را ارسال کن تا درخواست به‌صورت خودکار ثبت شود."),
            reply_markup=_kb_amount_selected(),
            parse_mode=ParseMode.HTML,
        )


@router.message(StateFilter(Topup.amount))
async def topup_amount_manual(m: Message, state: FSMContext):
    txt = (m.text or "").strip().replace(",", "").replace("٫", "").replace(" ", "")
    try:
        amount = int(txt)
    except Exception:
        return await m.reply(_with_card_info("لطفاً مبلغ را فقط با اعداد ارسال کن."), parse_mode=ParseMode.HTML)
    if amount <= 0:
        return await m.reply(_with_card_info("مبلغ باید بزرگ‌تر از صفر باشد."), parse_mode=ParseMode.HTML)
    await state.update_data(amount=amount, media=[], notes=[])
    await state.set_state(Topup.note)
    logger.info("Topup custom amount set uid=%s amount=%s", m.from_user.id, amount)
    await m.reply(
        _with_card_info(
            f"مبلغ انتخاب شد: <b>{format_toman(amount)}</b>\nرسید/توضیحات را بفرست؛ پس از دریافت عکس، درخواست به‌صورت خودکار ثبت می‌شود."
        ),
        reply_markup=_kb_amount_selected(),
        parse_mode=ParseMode.HTML,
    )


def _wallet_text(bal: int) -> str:
    return f"💰 موجودی فعلی شما: <b>{format_toman(bal)}</b>"


def _topup_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="topup")],
            [InlineKeyboardButton(text="↩️ منوی اصلی", callback_data="home")],
        ]
    )


@router.callback_query(F.data.regexp(r"^admin:pending:(\d+)$"))
async def admin_pending(cb: CallbackQuery):
    if not is_staff(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    page = int(re.match(r"^admin:pending:(\d+)$", cb.data).group(1))
    size = PAGE_SIZE_PAYMENTS
    off = page * size
    rows, total = db_list_pending_payments_page(off, size)
    kb_rows = []
    for r in rows:
        kb_rows.append([InlineKeyboardButton(text=f"#{r['id']} مبلغ {format_toman(r['amount'])}", callback_data=f"payview:{r['id']}")])
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


def _kb_payment_actions(pid: int, include_back: bool = True):
    rows = [
        [InlineKeyboardButton(text="✅ تایید و شارژ", callback_data=f"payok:{pid}")],
        [InlineKeyboardButton(text="✖️ رد پرداخت", callback_data=f"payno:{pid}")],
    ]
    if include_back:
        rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="admin:pending:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _annotate_payment_message(cb: CallbackQuery, note: str):
    try:
        if cb.message.caption is not None:
            await cb.message.edit_caption(f"{cb.message.caption}\n{note}", reply_markup=None)
        else:
            await cb.message.edit_text(f"{cb.message.text}\n{note}", reply_markup=None)
    except Exception:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^payview:(\d+)$"))
async def admin_pay_view(cb: CallbackQuery):
    if not is_staff(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    pid = int(re.match(r"^payview:(\d+)$", cb.data).group(1))
    r = db_get_payment(pid)
    if not r:
        return await cb.answer("درخواست پیدا نشد", show_alert=True)
    text = (
        f"درخواست #{pid}\n"
        f"کاربر: <a href=\"tg://user?id={r['user_id']}\">{r['user_id']}</a>\n"
        f"مبلغ: {format_toman(r['amount'])}\n"
    )
    if r.get("note"):
        text += f"توضیحات:\n{htmlesc(r['note'])}\n"
    try:
        media = json.loads(r.get("photos_json") or "[]")
    except Exception:
        media = []
    media = _normalize_media(media)
    if media:
        try:
            await cb.message.delete()
        except Exception:
            pass
        for i, item in enumerate(media):
            caption = text if i == 0 else None
            markup = _kb_payment_actions(pid) if i == 0 else None
            await _send_media(cb.bot, cb.from_user.id, item, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await cb.message.edit_text(text, reply_markup=_kb_payment_actions(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^payok:(\d+)$"))
async def admin_pay_ok(cb: CallbackQuery):
    if not is_staff(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    pid = int(re.match(r"^payok:(\d+)$", cb.data).group(1))
    r = db_get_payment(pid)
    if not r:
        return await cb.answer("درخواست پیدا نشد", show_alert=True)
    status = (r.get("status") or "").lower()
    if status == "approved":
        return await cb.answer("این پرداخت قبلاً تایید شده است.", show_alert=True)
    if status == "rejected":
        return await cb.answer("این پرداخت قبلاً رد شده است.", show_alert=True)
    db_add_wallet(r["user_id"], r["amount"])
    db_update_payment_status(pid, "approved")
    logger.info("Topup approved pid=%s uid=%s amount=%s by_admin=%s", pid, r["user_id"], r["amount"], cb.from_user.id)
    try:
        await cb.bot.send_message(r["user_id"], f"پرداخت شما به مبلغ {format_toman(r['amount'])} تایید شد و به کیف پولتان اضافه شد.")
    except Exception:
        pass
    actor_label = cb.from_user.full_name or cb.from_user.username or cb.from_user.id
    await _annotate_payment_message(cb, f"✅ تایید توسط {actor_label}")
    await cb.answer("پرداخت تایید شد.")
    if getattr(cb.message, "chat", None) and getattr(cb.message.chat, "type", "") == "private":
        await admin_pending(cb)


@router.callback_query(F.data.regexp(r"^payno:(\d+)$"))
async def admin_pay_no(cb: CallbackQuery):
    if not is_staff(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز است", show_alert=True)
    pid = int(re.match(r"^payno:(\d+)$", cb.data).group(1))
    r = db_get_payment(pid)
    if not r:
        return await cb.answer("درخواست پیدا نشد", show_alert=True)
    status = (r.get("status") or "").lower()
    if status == "approved":
        return await cb.answer("این پرداخت قبلاً تایید شده است.", show_alert=True)
    if status == "rejected":
        return await cb.answer("این پرداخت قبلاً رد شده است.", show_alert=True)
    db_update_payment_status(pid, "rejected")
    try:
        await cb.bot.send_message(r["user_id"], "پرداخت شما تایید نشد. لطفاً مجدد ارسال کنید یا با پشتیبانی در تماس باشید.")
    except Exception:
        pass
    actor_label = cb.from_user.full_name or cb.from_user.username or cb.from_user.id
    await _annotate_payment_message(cb, f"❌ رد توسط {actor_label}")
    await cb.answer("پرداخت رد شد.")
    if getattr(cb.message, "chat", None) and getattr(cb.message.chat, "type", "") == "private":
        await admin_pending(cb)
