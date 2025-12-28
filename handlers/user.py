import json
import logging
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from aiogram import Router, F
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest

from config import THREEXUI_INBOUND_ID, SUB_PATH, SUB_PORT, SUB_SCHEME, SUB_HOST, REQUIRED_CHANNEL
from db import (
    save_or_update_user,
    db_get_wallet,
    db_get_plans_for_user,
    db_get_plan,
    try_deduct_wallet,
    rollback_wallet,
    db_new_purchase,
    user_active_purchases,
    cache_get_usage,
    cache_set_usage,
    set_setting,
    get_setting,
    cur,
    is_admin,
    is_support,
    get_active_purchase_for_inbound,
    mark_purchase_superseded,
    get_global_discount_percent,
    log_event,
    user_has_test_purchase,
    inc_referral_click,
    inc_referral_signup,
)
from keyboards import kb_main, kb_force_join, kb_plans, kb_mysubs, kb_sub_detail
from utils import (
    htmlesc,
    progress_bar,
    human_bytes,
    qr_bytes,
    safe_name_from_user,
    parse_channel_list,
    fetch_channel_details,
    format_toman,
)
from xui import three_session

TZ = timezone.utc
router = Router()
logger = logging.getLogger("pingx.user")


class Topup(StatesGroup):
    amount = State()
    note = State()


def _plan_flags(plan) -> dict:
    try:
        return json.loads(plan.get("flags") or "{}")
    except Exception:
        return {}


def _apply_discount(price: int) -> tuple[int, int]:
    pct = get_global_discount_percent()
    final = price
    if pct > 0:
        final = int(price * (100 - pct) / 100)
    return max(final, 0), pct


def _kb_main_for(uid: int):
    return kb_main(uid, is_admin(uid), is_support(uid))


async def _deliver_subscription_link(bot, uid: int, link: str):
    await bot.send_photo(
        uid,
        BufferedInputFile(qr_bytes(link).getvalue(), filename="pingx.png"),
        caption="🔗 QR و لینک اشتراک شما:",
    )
    await bot.send_message(
        uid,
        f"<a href=\"{htmlesc(link)}\">مشاهده لینک</a>\n<code>{link}</code>",
        parse_mode=ParseMode.HTML,
    )


async def _resolve_subscription_link(row, mode: str = "panel") -> str:
    """
    Resolve a subscription link, optionally refreshing from panel or rotating subId.
    mode:
      - panel (default): fetch from panel if possible
      - local: use cached link if present, otherwise panel
      - rotate: rotate subId on panel and update DB
    """
    link = row.get("sub_link")
    if mode == "local" and link:
        return link
    if not three_session:
        raise RuntimeError("panel_unavailable")
    inbound_id = int(row["three_xui_inbound_id"])
    client_id = row["three_xui_client_id"]
    client_email = row["client_email"] if "client_email" in row.keys() else None
    sub_id = row.get("sub_id")
    expiry_ms = int(row.get("expiry_ms") or 0)
    current = None
    if mode != "rotate":
        try:
            current = await three_session.get_client_stats(inbound_id, client_id, client_email)
        except Exception:
            current = None
        if current:
            sub_id = current.get("subId") or sub_id
            expiry_ms = int(current.get("expiryTime") or expiry_ms or 0)
    if mode == "rotate" or not sub_id:
        if mode == "rotate" and not current:
            raise RuntimeError("client_not_found_on_panel")
        sub_id = await three_session.rotate_subid(inbound_id, client_id, email=client_email)
    link = build_subscribe_url(sub_id)
    if current or mode == "rotate":
        cur.execute("UPDATE purchases SET sub_id=?, sub_link=?, expiry_ms=? WHERE id=?", (sub_id, link, expiry_ms, row["id"]))
    return link


def build_subscribe_url(sub_id: str) -> str:
    host_cfg = (get_setting("SUB_HOST", SUB_HOST) or "").strip()
    scheme = (get_setting("SUB_SCHEME", SUB_SCHEME) or SUB_SCHEME or "https").strip() or "https"
    path = (get_setting("SUB_PATH", SUB_PATH) or SUB_PATH or "/").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    base = three_session.base if (three_session and getattr(three_session, "base", None)) else ""
    parsed_base = urlparse(base) if base else None
    host = host_cfg or (parsed_base.hostname if parsed_base else "localhost")
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE).strip("/")
    port_raw = str(get_setting("SUB_PORT", str(SUB_PORT)) or "").strip()
    port = None
    try:
        port_val = int(port_raw)
        if port_val > 0:
            port = port_val
    except Exception:
        port = None
    if port is None:
        port = parsed_base.port if parsed_base and parsed_base.port else SUB_PORT
    return f"{scheme}://{host}:{port}{path}{sub_id}"


def _required_channels_list() -> list[str]:
    raw = (get_setting("REQUIRED_CHANNELS", "").strip() or get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL) or REQUIRED_CHANNEL)
    return parse_channel_list(raw)


async def check_force_join(bot, uid: int) -> bool:
    channels = _required_channels_list()
    if not channels:
        return True
    for ch in channels:
        try:
            cm = await bot.get_chat_member(ch, uid)
            status = getattr(cm, "status", None)
            if status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                return False
        except Exception:
            return False
    return True


async def _force_join_message(bot):
    channels = _required_channels_list()
    details = await fetch_channel_details(bot, channels)
    lines = "\n".join(f"• {d.get('label')}" for d in details if d.get("label"))
    text = "📢 برای استفاده از ربات لطفاً ابتدا در کانال‌های زیر عضو شوید."
    if lines:
        text += f"\n{lines}"
    return text, kb_force_join(details)


@router.message(CommandStart())
async def start(m: Message):
    if getattr(m.chat, "type", "private") != "private":
        return
    # referral tracking
    raw_text = m.text or ""
    parts = raw_text.split(maxsplit=1)
    ref_param = parts[1].strip() if len(parts) > 1 else ""
    ref_code = None
    if ref_param:
        ref_code = ref_param.replace("ref-", "", 1) if ref_param.startswith("ref-") else ref_param
        if ref_code:
            inc_referral_click(ref_code)
    existed = cur.execute("SELECT 1 FROM users WHERE user_id=?", (m.from_user.id,)).fetchone() is not None
    save_or_update_user(m.from_user)
    if ref_code and not existed:
        inc_referral_signup(ref_code, m.from_user)
    log_event(m.from_user.id, "start", {})
    if not await check_force_join(m.bot, m.from_user.id):
        text, markup = await _force_join_message(m.bot)
        await m.answer(text, reply_markup=markup)
        return
    bal = db_get_wallet(m.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    await m.answer(
        welcome + f"\n\n💰 موجودی کیف پول: <b>{format_toman(bal)}</b>",
        reply_markup=_kb_main_for(m.from_user.id),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    bal = db_get_wallet(cb.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    await cb.message.edit_text(
        welcome + f"\n\n💰 موجودی کیف پول: <b>{format_toman(bal)}</b>",
        reply_markup=_kb_main_for(cb.from_user.id),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "buy")
async def buy_menu(cb: CallbackQuery):
    is_adm = is_admin(cb.from_user.id)
    plans = db_get_plans_for_user(is_adm)
    if user_has_test_purchase(cb.from_user.id) and not is_adm:
        plans = [p for p in plans if not _plan_flags(p).get("test")]
    discount_pct = get_global_discount_percent()
    log_event(cb.from_user.id, "view_plans", {"discount_pct": discount_pct})
    await cb.message.edit_text("🎯 یکی از پلن‌های زیر را انتخاب کن:", reply_markup=kb_plans(plans, is_adm, discount_pct))


@router.callback_query(F.data.startswith("plan:"))
async def plan_select(cb: CallbackQuery):
    pid = cb.data.split(":")[1]
    plan = db_get_plan(pid)
    if not plan:
        return await cb.answer("پلن پیدا نشد")
    flags = _plan_flags(plan)
    if flags.get("test") and not is_admin(cb.from_user.id) and user_has_test_purchase(cb.from_user.id):
        return await cb.answer("شما قبلا پلن آزمایشی دریافت کرده‌ای.", show_alert=True)
    orig_price = int(plan["price"])
    price, discount_pct = _apply_discount(orig_price)
    bal = db_get_wallet(cb.from_user.id)
    if bal < price:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="topup")],
                [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="buy")],
            ]
        )
        await cb.message.edit_text(
            "⚠️ موجودی کیف پول برای این خرید کافی نیست:\n"
            f"• مبلغ پلن: <b>{format_toman(price)}</b>"
            + (f" (با تخفیف {discount_pct}% از {format_toman(orig_price)})" if discount_pct else "")
            + "\n"
            f"• موجودی فعلی: <b>{format_toman(bal)}</b>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="تایید و ادامه خرید", callback_data=f"confirm:{pid}")],
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="buy")],
        ]
    )
    await cb.message.edit_text(
        f"🛒 پلن انتخابی: <b>{plan['title']}</b>\n💵 مبلغ: <b>{format_toman(price)}</b>"
        + (f" (با تخفیف {discount_pct}% از {format_toman(orig_price)})" if discount_pct else "")
        + "\nآیا تایید می‌کنی؟",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("confirm:"))
async def buy_confirm(cb: CallbackQuery):
    pid = cb.data.split(":")[1]
    plan = db_get_plan(pid)
    if not plan:
        return await cb.answer("پلن پیدا نشد")
    flags = _plan_flags(plan)
    if flags.get("test") and not is_admin(cb.from_user.id) and user_has_test_purchase(cb.from_user.id):
        return await cb.answer("شما قبلا پلن آزمایشی دریافت کرده‌ای.", show_alert=True)
    orig_price = int(plan["price"])
    price, discount_pct = _apply_discount(orig_price)
    log_event(
        cb.from_user.id,
        "checkout_initiated",
        {"plan_id": pid, "price": price, "orig_price": orig_price, "discount_pct": discount_pct},
    )
    if not try_deduct_wallet(cb.from_user.id, price):
        logger.warning("Buy failed insufficient wallet uid=%s plan=%s price=%s", cb.from_user.id, pid, price)
        return await cb.answer("موجودی کافی نیست.", show_alert=True)
    if not three_session:
        rollback_wallet(cb.from_user.id, price)
        await cb.message.edit_text("🚫 اتصال به سرور اشتراک برقرار نشد. لطفاً بعداً تلاش کن یا به پشتیبانی اطلاع بده.")
        return
    inbound_id = int(get_setting("ACTIVE_INBOUND_ID", str(THREEXUI_INBOUND_ID)))
    base_email = safe_name_from_user(cb.from_user)
    plan_slug = re.sub(r"[^A-Za-z0-9]+", "-", plan["title"]).strip("-") or "plan"
    name_part, _, domain_part = base_email.partition("@")
    domain_part = domain_part or "telegram"
    uniq = secrets.token_hex(2)
    email = f"{name_part}-{plan_slug}-{uniq}@{domain_part}"
    remark = f"{(cb.from_user.full_name or cb.from_user.username or cb.from_user.id)} | {plan['title']} | {cb.from_user.id}"
    device_limit = int(flags.get("device_limit") or 0)
    meta_value = {"test": True} if flags.get("test") else {}
    now_ms = int(datetime.now(TZ).timestamp() * 1000)
    active_purchase = get_active_purchase_for_inbound(cb.from_user.id, inbound_id, now_ms)
    sub_link = None
    try:
        if active_purchase:
            client_id = active_purchase["three_xui_client_id"]
            client_email = active_purchase.get("client_email")
            stat = await three_session.get_client_stats(inbound_id, client_id, client_email)
            if not stat:
                raise RuntimeError("panel_stat_missing")
            current_total = int(stat.get("total") or 0)
            add_total = int(plan["gb"] or 0) * 1024**3
            new_total = current_total + add_total
            cur_expiry = int(stat.get("expiryTime") or 0)
            base_expiry = cur_expiry if cur_expiry > now_ms else now_ms
            new_expiry = base_expiry + int(plan["days"] or 0) * 24 * 3600 * 1000
            payload = dict(stat)
            payload["total"] = new_total
            payload["expiryTime"] = new_expiry
            await three_session.update_client(inbound_id, payload.get("id") or client_id, payload)
            sub_id = active_purchase.get("sub_id") or payload.get("subId")
            if not sub_id:
                sub_id = await three_session.rotate_subid(inbound_id, payload.get("id") or client_id, email=client_email)
            sub_link = active_purchase.get("sub_link") or build_subscribe_url(sub_id)
            meta_json = dict(meta_value)
            meta_json["renewed_from"] = active_purchase["id"]
            new_pid = db_new_purchase(
                user_id=cb.from_user.id,
                plan_id=plan["id"],
                price=price,
                three_xui_client_id=client_id,
                three_xui_inbound_id=str(inbound_id),
                client_email=client_email,
                sub_id=sub_id,
                sub_link=sub_link,
                allocated_gb=int(plan["gb"] or 0),
                expiry_ms=new_expiry,
                meta=json.dumps(meta_json, ensure_ascii=False) if meta_json else None,
            )
            mark_purchase_superseded(active_purchase["id"], new_pid)
            cur.execute(
                "UPDATE purchases SET active=0 WHERE user_id=? AND three_xui_inbound_id=? AND id<>?",
                (cb.from_user.id, str(inbound_id), new_pid),
            )
            cache_set_usage(new_pid, int(stat.get("up") or 0), int(stat.get("down") or 0), new_total, new_expiry)
        else:
            added = await three_session.add_client(
                inbound_id,
                email=email,
                expire_days=int(plan["days"]),
                data_gb=int(plan["gb"]),
                remark=remark,
                limit_ip=device_limit,
            )
            client = added["client"]
            client_id = client["id"]
            sub_id = client.get("subId") or secrets.token_hex(6)
            if not client.get("subId"):
                c2 = dict(client)
                c2["subId"] = sub_id
                await three_session.update_client(inbound_id, client_id, c2)
            sub_link = build_subscribe_url(sub_id)
            expiry_ms = int(client.get("expiryTime") or 0)
            allocated_gb = int(plan["gb"] or 0)
            db_new_purchase(
                user_id=cb.from_user.id,
                plan_id=plan["id"],
                price=price,
                three_xui_client_id=client_id,
                three_xui_inbound_id=str(inbound_id),
                client_email=email,
                sub_id=sub_id,
                sub_link=sub_link,
                allocated_gb=allocated_gb,
                expiry_ms=expiry_ms,
                meta=json.dumps(meta_value, ensure_ascii=False) if meta_value else None,
            )
    except Exception as e:
        rollback_wallet(cb.from_user.id, price)
        await cb.message.edit_text(f"❌ خطا در ایجاد/تمدید کانکشن:\n<code>{htmlesc(str(e))}</code>", parse_mode=ParseMode.HTML)
        logger.exception("Add/renew client failed uid=%s plan=%s", cb.from_user.id, pid)
        return
    log_event(cb.from_user.id, "purchase_success", {"plan_id": plan["id"], "paid_price": price})
    try:
        await _deliver_subscription_link(cb.bot, cb.from_user.id, sub_link)
    except Exception:
        logger.warning("Failed to send link/qr uid=%s", cb.from_user.id, exc_info=True)
    success_text = get_setting(
        "POST_PURCHASE_TEMPLATE",
        "✅ خرید انجام شد. لینک اشتراک برای شما ارسال شد.",
    )
    try:
        await cb.message.edit_text(
            success_text,
            reply_markup=_kb_main_for(cb.from_user.id),
        )
    except Exception:
        await cb.bot.send_message(
            cb.from_user.id,
            success_text,
            reply_markup=_kb_main_for(cb.from_user.id),
        )


@router.callback_query(F.data == "mysubs")
async def mysubs(cb: CallbackQuery):
    rows = user_active_purchases(cb.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="home")],
        ]
    )
    if not rows:
        await cb.message.edit_text("❌ هیچ اشتراکی برای شما ثبت نشده است.", reply_markup=kb)
        return
    await cb.message.edit_text("📜 اشتراک‌های شما:", reply_markup=kb_mysubs(rows))


@router.callback_query(F.data.startswith("sub:"))
async def sub_detail(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("این اشتراک یافت نشد یا برای شما نیست.", show_alert=True)
    stat = cache_get_usage(pid)
    usage_txt = ""
    if stat:
        used = int(stat.get("up") or 0) + int(stat.get("down") or 0)
        total = int(stat.get("total") or 0)
        expiry = int(stat.get("expiry_ms") or 0) or int(r["expiry_ms"] or 0)
        bar = progress_bar(used / total) if total > 0 else ""
        exp_txt = datetime.fromtimestamp(expiry / 1000, tz=TZ).strftime("%Y-%m-%d %H:%M") if expiry else "نامشخص"
        total_hr = "نامحدود" if total <= 0 else human_bytes(total)
        usage_txt = f"📊 مصرف: {human_bytes(used)} / {total_hr}\n{bar}\n⏰ انقضا: {exp_txt}"
    text = f"<b>اشتراک #{r['id']}</b>\n"
    text += f"پلن: {htmlesc(r['plan_id'])} | مبلغ: {format_toman(r['price'])}\n"
    if usage_txt:
        text += usage_txt
    await cb.message.edit_text(text, reply_markup=kb_sub_detail(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("subfix:"))
async def sub_fix_link(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("این اشتراک یافت نشد یا برای شما نیست.", show_alert=True)
    inbound_id = int(r["three_xui_inbound_id"])
    client_id = r["three_xui_client_id"]
    if not three_session:
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    try:
        link = await _resolve_subscription_link(dict(r), mode="rotate")
        await _deliver_subscription_link(cb.bot, cb.from_user.id, link)
        try:
            await cb.answer("لینک جدید صادر شد.")
        except TelegramBadRequest:
            # callback timed out; ارسال پیام معمولی
            await cb.message.answer("لینک جدید صادر شد.")
    except RuntimeError as e:
        if str(e) == "client_not_found_on_panel":
            return await cb.answer("این اشتراک روی پنل فعلی یافت نشد، نمی‌توان لینک را تغییر داد.", show_alert=True)
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    except Exception:
        logger.exception("rotate_subid failed pid=%s uid=%s", pid, cb.from_user.id)
        try:
            await cb.answer("ارسال لینک با خطا مواجه شد.", show_alert=True)
        except TelegramBadRequest:
            await cb.message.answer("ارسال لینک با خطا مواجه شد.")


@router.callback_query(F.data.startswith("sublink:"))
async def sub_show_link(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("این اشتراک یافت نشد یا برای شما نیست.", show_alert=True)
    try:
        link = await _resolve_subscription_link(dict(r), mode="panel")
        await _deliver_subscription_link(cb.bot, cb.from_user.id, link)
        await cb.answer("لینک برای شما ارسال شد.")
    except RuntimeError:
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    except Exception:
        logger.exception("send sub link failed pid=%s uid=%s", pid, cb.from_user.id)
        await cb.answer("ارسال لینک با خطا مواجه شد.", show_alert=True)


@router.callback_query(F.data.startswith("substat:"))
async def sub_stat_refresh(cb: CallbackQuery):
    if not three_session:
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("این اشتراک یافت نشد یا برای شما نیست.", show_alert=True)
    inbound_id = int(r["three_xui_inbound_id"])
    client_id = r["three_xui_client_id"]
    stat = await three_session.get_client_stats(inbound_id, client_id, r["client_email"])
    if not stat:
        return await cb.answer("آمار مصرفی در دسترس نیست", show_alert=True)
    total = int(stat.get("total") or 0)
    if total <= 0 and int(r["allocated_gb"] or 0) > 0:
        total = int(r["allocated_gb"]) * 1024 ** 3
    expiry = int(stat.get("expiryTime") or r["expiry_ms"] or 0)
    cache_set_usage(pid, int(stat.get("up") or 0), int(stat.get("down") or 0), total, expiry)
    await cb.answer("✅ آمار به‌روز شد")
    await sub_detail(cb)


@router.callback_query(F.data == "recheck_join")
async def recheck_join(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    channels = _required_channels_list()
    if not await check_force_join(cb.bot, cb.from_user.id):
        text, markup = await _force_join_message(cb.bot)
        try:
            await cb.message.edit_text(text, reply_markup=markup)
        except Exception:
            await cb.message.answer(text, reply_markup=markup)
        return
    bal = db_get_wallet(cb.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    await cb.message.edit_text(
        welcome + f"\n\n💰 موجودی کیف پول: <b>{format_toman(bal)}</b>",
        reply_markup=_kb_main_for(cb.from_user.id),
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(None))
async def fallback_main_menu(m: Message):
    if getattr(m.chat, "type", "private") != "private":
        return
    if m.text and str(m.text).startswith("/"):
        return
    row = cur.execute(
        "SELECT 1 FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (m.from_user.id,),
    ).fetchone()
    if row:
        return
    bal = db_get_wallet(m.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدید!")
    logger.info("Fallback main menu uid=%s state=None text=%s", m.from_user.id, (m.text or "")[:200])
    await m.answer(
        welcome + f"\n\n💰 موجودی فعلی شما: <b>{format_toman(bal)}</b>",
        reply_markup=_kb_main_for(m.from_user.id),
        parse_mode=ParseMode.HTML,
    )
