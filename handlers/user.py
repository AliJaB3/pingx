import json
import logging
import re
import secrets
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter

from config import THREEXUI_INBOUND_ID, SUB_PATH, SUB_PORT, SUB_SCHEME, SUB_HOST, REQUIRED_CHANNEL
from db import (
    save_or_update_user,
    db_get_wallet,
    db_get_plans_for_user,
    db_get_plan,
    try_deduct_wallet,
    rollback_wallet,
    db_new_purchase,
    user_purchases,
    cache_get_usage,
    set_setting,
    get_setting,
    cur,
    is_admin,
)
from keyboards import kb_main, kb_force_join, kb_plans, kb_mysubs, kb_sub_detail
from utils import htmlesc, progress_bar, human_bytes, qr_bytes, safe_name_from_user, parse_channel_list
from xui import three_session

TZ = timezone.utc
router = Router()
logger = logging.getLogger("pingx.user")
SUBSTAT_LOCK = asyncio.Lock()


class Topup(StatesGroup):
    amount = State()
    note = State()


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


async def _resolve_subscription_link(row, mode: str = "local") -> str:
    link = row.get("sub_link")
    if mode == "local" and link:
        return link
    if not three_session:
        raise RuntimeError("panel_unavailable")
    inbound_id = int(row["three_xui_inbound_id"])
    client_id = row["three_xui_client_id"]
    client_email = row["client_email"] if "client_email" in row.keys() else None
    sub_id = row.get("sub_id")
    if mode != "rotate":
        try:
            curr = await three_session.get_client_stats(inbound_id, client_id, client_email)
        except Exception:
            curr = None
        if curr:
            sub_id = curr.get("subId") or sub_id
    if mode == "rotate" or not sub_id:
        sub_id = await three_session.rotate_subid(inbound_id, client_id, email=client_email)
    link = build_subscribe_url(sub_id)
    cur.execute("UPDATE purchases SET sub_id=?, sub_link=? WHERE id=?", (sub_id, link, row["id"]))
    return link


def build_subscribe_url(sub_id: str) -> str:
    host = (get_setting("SUB_HOST", SUB_HOST) or "").strip()
    if not host:
        host = (three_session and three_session.base.split("://")[-1].split(":")[0]) or "localhost"
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE).strip("/")
    scheme = (get_setting("SUB_SCHEME", SUB_SCHEME) or SUB_SCHEME or "https").strip() or "https"
    path = (get_setting("SUB_PATH", SUB_PATH) or SUB_PATH or "/").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    port_raw = str(get_setting("SUB_PORT", str(SUB_PORT)) or "").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = SUB_PORT
    if port <= 0:
        port = SUB_PORT
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


@router.message(CommandStart())
async def start(m: Message):
    if getattr(m.chat, "type", "private") != "private":
        return
    save_or_update_user(m.from_user)
    if not await check_force_join(m.bot, m.from_user.id):
        await m.answer(
            "📢 برای استفاده از ربات لطفاً ابتدا در کانال‌های اعلام‌شده عضو شوید.",
            reply_markup=kb_force_join(_required_channels_list()),
        )
        return
    bal = db_get_wallet(m.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    await m.answer(
        welcome + f"\n\n💰 موجودی کیف پول: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(m.from_user.id, is_admin(m.from_user.id)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    bal = db_get_wallet(cb.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    await cb.message.edit_text(
        welcome + f"\n\n💰 موجودی کیف پول: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "buy")
async def buy_menu(cb: CallbackQuery):
    plans = db_get_plans_for_user(is_admin(cb.from_user.id))
    await cb.message.edit_text("🎯 یکی از پلن‌های زیر را انتخاب کن:", reply_markup=kb_plans(plans, is_admin(cb.from_user.id)))


@router.callback_query(F.data.startswith("plan:"))
async def plan_select(cb: CallbackQuery):
    pid = cb.data.split(":")[1]
    plan = db_get_plan(pid)
    if not plan:
        return await cb.answer("پلن پیدا نشد")
    price = int(plan["price"])
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
            f"• مبلغ پلن: <b>{price:,}</b> تومان\n"
            f"• موجودی فعلی: <b>{bal:,}</b> تومان",
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
        f"🛒 پلن انتخابی: <b>{plan['title']}</b>\n💵 مبلغ: <b>{price:,}</b> تومان\nآیا تایید می‌کنی؟",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("confirm:"))
async def buy_confirm(cb: CallbackQuery):
    pid = cb.data.split(":")[1]
    plan = db_get_plan(pid)
    if not plan:
        return await cb.answer("پلن پیدا نشد")
    price = int(plan["price"])
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
    flags = {}
    try:
        flags = json.loads(plan.get("flags") or "{}")
    except Exception:
        flags = {}
    device_limit = int(flags.get("device_limit") or 0)
    try:
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
    except Exception as e:
        rollback_wallet(cb.from_user.id, price)
        await cb.message.edit_text(f"خطا در ایجاد اشتراک:\n<code>{htmlesc(str(e))}</code>", parse_mode=ParseMode.HTML)
        logger.exception("Add client failed uid=%s plan=%s", cb.from_user.id, pid)
        return
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
        meta=None,
    )
    try:
        await _deliver_subscription_link(cb.bot, cb.from_user.id, sub_link)
    except Exception:
        logger.warning("Failed to send link/qr uid=%s", cb.from_user.id, exc_info=True)
    await cb.message.edit_text(
        get_setting("POST_PURCHASE_TEMPLATE", "✅ خرید انجام شد. لینک اشتراک برای شما ارسال شد."),
        reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)),
    )


@router.callback_query(F.data == "mysubs")
async def mysubs(cb: CallbackQuery):
    rows = user_purchases(cb.from_user.id)
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
    text += f"پلن: {htmlesc(r['plan_id'])} | مبلغ: {r['price']:,} تومان\n"
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
        await cb.answer("لینک جدید صادر شد.")
    except RuntimeError:
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    except Exception:
        logger.exception("rotate_subid failed pid=%s uid=%s", pid, cb.from_user.id)
        await cb.answer("ارسال لینک با خطا مواجه شد.", show_alert=True)


@router.callback_query(F.data.startswith("sublink:"))
async def sub_show_link(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("این اشتراک یافت نشد یا برای شما نیست.", show_alert=True)
    try:
        link = await _resolve_subscription_link(dict(r), mode="local")
        await _deliver_subscription_link(cb.bot, cb.from_user.id, link)
        await cb.answer("لینک برای شما ارسال شد.")
    except RuntimeError:
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    except Exception:
        logger.exception("send sub link failed pid=%s uid=%s", pid, cb.from_user.id)
        await cb.answer("ارسال لینک با خطا مواجه شد.", show_alert=True)


@router.callback_query(F.data.startswith("subrevoke:"))
async def sub_revoke(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("این اشتراک یافت نشد یا برای شما نیست.", show_alert=True)
    if not three_session:
        return await cb.answer("اتصال به سرور اشتراک برقرار نیست.", show_alert=True)
    try:
        inbound_id = int(r["three_xui_inbound_id"])
        client_id = r["three_xui_client_id"]
        client_email = r["client_email"] if "client_email" in r.keys() else None
        # Fetch full payload to avoid wiping other fields on update
        inbound = await three_session.get_inbound(inbound_id)
        payload = None
        if inbound:
            try:
                s = inbound.get("settings")
                s = json.loads(s) if isinstance(s, str) else (s or {})
                payload = next(
                    (c for c in (s.get("clients") or []) if str(c.get("id")).replace("-", "") == str(client_id).replace("-", "")),
                    None,
                )
            except Exception:
                payload = None
        if not payload and client_email:
            payload = await three_session.get_client_stats(inbound_id, client_id, client_email)
        if not payload:
            return await cb.answer("کلاینت پیدا نشد.", show_alert=True)
        real_id = payload.get("id") or client_id
        payload = dict(payload)
        payload["enable"] = False
        await three_session.update_client(inbound_id, real_id, payload)
        cur.execute("UPDATE purchases SET meta=? WHERE id=?", ("revoked", pid))
        await cb.message.edit_text("🚫 اشتراک غیرفعال شد.", reply_markup=kb_mysubs(user_purchases(cb.from_user.id)))
    except Exception as e:
        await cb.answer(f"خطا: {e}", show_alert=True)


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
    if SUBSTAT_LOCK.locked():
        return await cb.answer("در حال بررسی یک اشتراک دیگر هستیم. لطفاً چند لحظه دیگر تلاش کن.", show_alert=True)
    async with SUBSTAT_LOCK:
        stat = await three_session.get_client_stats(inbound_id, client_id, r["client_email"])
    if not stat:
        return await cb.answer("آمار مصرفی در دسترس نیست", show_alert=True)
    total = int(stat.get("total") or 0)
    if total <= 0 and int(r["allocated_gb"] or 0) > 0:
        total = int(r["allocated_gb"]) * 1024 ** 3
    expiry = int(stat.get("expiryTime") or r["expiry_ms"] or 0)
    from db import cache_set_usage

    cache_set_usage(pid, int(stat.get("up") or 0), int(stat.get("down") or 0), total, expiry)
    await cb.answer("✅ آمار به‌روز شد")
    await sub_detail(cb)


@router.callback_query(F.data == "recheck_join")
async def recheck_join(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    channels = _required_channels_list()
    if not await check_force_join(cb.bot, cb.from_user.id):
        await cb.message.edit_text(
            "📢 هنوز عضو تمام کانال‌های موردنیاز نشده‌ای.",
            reply_markup=kb_force_join(channels),
        )
        return
    bal = db_get_wallet(cb.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    await cb.message.edit_text(
        welcome + f"\n\n💰 موجودی کیف پول: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)),
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
        welcome + f"\n\n💰 موجودی فعلی شما: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(m.from_user.id, is_admin(m.from_user.id)),
        parse_mode=ParseMode.HTML,
    )
