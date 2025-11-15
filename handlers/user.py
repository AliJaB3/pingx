from aiogram import Router, F
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from db import is_admin, get_admin_ids
from config import THREEXUI_INBOUND_ID, SUB_PATH, SUB_PORT, SUB_SCHEME, SUB_HOST, REQUIRED_CHANNEL
from db import (
    save_or_update_user,
    db_get_wallet, db_get_plans_for_user, db_get_plan,
    try_deduct_wallet, rollback_wallet,
    db_new_purchase, user_purchases, cache_get_usage,
    set_setting, get_setting, cur, log_evt,
)
from keyboards import kb_main, kb_force_join, kb_plans, kb_mysubs, kb_sub_detail
from utils import htmlesc, progress_bar, human_bytes, qr_bytes, safe_name_from_user
from xui import three_session

import secrets
from datetime import datetime, timezone

TZ = timezone.utc
router = Router()


class Topup(StatesGroup):
    amount = State()
    note = State()


def build_subscribe_url(sub_id: str) -> str:
    host = (get_setting("SUB_HOST", SUB_HOST) or "").strip()
    if not host:
        host = (three_session and three_session.base.split("://")[-1].split(":")[0]) or "localhost"
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


async def check_force_join(bot, uid: int) -> bool:
    ch = get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL)
    if not ch:
        return True
    try:
        cm = await bot.get_chat_member(ch, uid)
        return cm.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return True


@router.message(CommandStart())
async def start(m: Message):
    if getattr(m.chat, "type", "private") != "private":
        return
    save_or_update_user(m.from_user)
    if not await check_force_join(m.bot, m.from_user.id):
        await m.answer(
            "برای ادامه لطفاً ابتدا در کانال اطلاع‌رسانی عضو شوید.",
            reply_markup=kb_force_join(get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL)),
        )
        return
    bal = db_get_wallet(m.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "به پینگ‌اِکس خوش آمدید!")
    await m.answer(
        welcome + f"\n\nموجودی شما: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(m.from_user.id, is_admin(m.from_user.id)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    bal = db_get_wallet(cb.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "به پینگ‌اِکس خوش آمدید!")
    await cb.message.edit_text(
        welcome + f"\n\nموجودی شما: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "buy")
async def buy_menu(cb: CallbackQuery):
    plans = db_get_plans_for_user(is_admin(cb.from_user.id))
    await cb.message.edit_text("یکی از پلن‌های زیر را انتخاب کنید:", reply_markup=kb_plans(plans, is_admin(cb.from_user.id)))


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
                [InlineKeyboardButton(text="افزایش موجودی 💳", callback_data="topup")],
                [InlineKeyboardButton(text="بازگشت ↩️", callback_data="buy")],
            ]
        )
        await cb.message.edit_text(
            "موجودی کیف پول کافی نیست:\n"
            f"• قیمت پلن: <b>{price:,}</b> تومان\n"
            f"• موجودی شما: <b>{bal:,}</b> تومان",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="تایید و ادامه ✅", callback_data=f"confirm:{pid}")],
            [InlineKeyboardButton(text="بازگشت ↩️", callback_data="buy")],
        ]
    )
    await cb.message.edit_text(
        f"پلن انتخاب‌شده: <b>{plan['title']}</b>\nقیمت: <b>{price:,}</b> تومان\nبرای ادامه تایید کنید.",
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
        return await cb.answer("موجودی کافی نیست.", show_alert=True)
    if not three_session:
        rollback_wallet(cb.from_user.id, price)
        await cb.message.edit_text("اتصال به سرور برقرار نشد. لطفاً کمی بعد دوباره تلاش کنید.")
        return
    inbound_id = int(get_setting("ACTIVE_INBOUND_ID", str(THREEXUI_INBOUND_ID)))
    email = safe_name_from_user(cb.from_user)
    remark = f"{(cb.from_user.full_name or cb.from_user.username or cb.from_user.id)} | {cb.from_user.id}"
    try:
        added = await three_session.add_client(
            inbound_id,
            email=email,
            expire_days=int(plan["days"]),
            data_gb=int(plan["gb"]),
            remark=remark,
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
        return
    pid2 = db_new_purchase(
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
        await cb.bot.send_photo(
            cb.from_user.id,
            BufferedInputFile(qr_bytes(sub_link).getvalue(), filename="pingx.png"),
            caption="QR کد یا لینک زیر را استفاده کنید.",
        )
        await cb.bot.send_message(
            cb.from_user.id,
            f"🔗 <a href=\"{htmlesc(sub_link)}\">باز کردن لینک اشتراک</a>\n<code>{sub_link}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    extra = get_setting("PURCHASE_SUCCESS_TEMPLATE", get_setting("POST_PURCHASE_TEMPLATE", "")).strip()
    if extra:
        await cb.bot.send_message(cb.from_user.id, extra)
    log_evt(cb.from_user.id, "purchase_confirm", {"purchase_id": pid2, "plan_id": plan["id"], "inbound_id": inbound_id})
    await cb.message.edit_text(
        "خرید با موفقیت انجام شد ✅",
        reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)),
    )


@router.callback_query(F.data == "mysubs")
async def mysubs(cb: CallbackQuery):
    rows = user_purchases(cb.from_user.id)
    if not rows:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="خرید اشتراک 🛒", callback_data="buy")],
                [InlineKeyboardButton(text="بازگشت ↩️", callback_data="home")],
            ]
        )
        await cb.message.edit_text("هنوز اشتراکی تهیه نکرده‌اید.", reply_markup=kb)
        return
    await cb.message.edit_text("اشتراک‌های شما:", reply_markup=kb_mysubs(rows))


@router.callback_query(F.data.startswith("sub:"))
async def sub_detail(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("دسترسی مجاز نیست.", show_alert=True)
    cached = cache_get_usage(pid)
    usage_txt = "آمار مصرف هنوز ثبت نشده است. از دکمه «وضعیت مصرف» برای بروزرسانی استفاده کنید."
    if cached:
        up = int(cached.get("up") or 0)
        down = int(cached.get("down") or 0)
        total = int(cached.get("total") or 0)
        used = up + down
        pct = 0.0 if total <= 0 else min(1.0, used / total)
        bar = progress_bar(pct)
        total_hr = "نامحدود" if total <= 0 else human_bytes(total)
        exp_ts = int((cached.get("expiry_ms") or r["expiry_ms"] or 0) / 1000)
        exp_txt = datetime.fromtimestamp(exp_ts, TZ).strftime('%Y-%m-%d %H:%M') if exp_ts else "-"
        usage_txt = f"مصرف: {human_bytes(used)} / {total_hr} ({int(pct*100)}%)\n{bar}\nانقضا: {exp_txt}"
    text = (
        f"<b>اشتراک #{r['id']}</b>\n"
        f"پلن: {htmlesc(r['plan_id'])} | مبلغ: {r['price']:,} تومان\n"
        f"Inbound: {r['three_xui_inbound_id']} | ClientId: <code>{r['three_xui_client_id']}</code>\n"
        f"SubId: <code>{r['sub_id'] or '-'}</code>\n\n{usage_txt}"
    )
    await cb.message.edit_text(text, reply_markup=kb_sub_detail(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("subfix:"))
async def sub_fix_link(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("اجازه دسترسی ندارید")
    link = build_subscribe_url(r["sub_id"]) if r["sub_id"] else r["sub_link"]
    try:
        await cb.bot.send_photo(
            cb.from_user.id,
            BufferedInputFile(qr_bytes(link).getvalue(), filename=f"pingx-{pid}.png"),
            caption="کد QR یا لینک:",
        )
    except Exception:
        pass
    await cb.bot.send_message(cb.from_user.id, f"<a href=\"{htmlesc(link)}\">باز کردن لینک اشتراک</a>\n<code>{link}</code>", parse_mode=ParseMode.HTML)
    await cb.answer("لینک ارسال شد ✅")


@router.callback_query(F.data.startswith("subrevoke:"))
async def sub_revoke(cb: CallbackQuery):
    if not three_session:
        return await cb.answer("اتصال به پنل تنظیم نشده", show_alert=True)
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("اجازه دسترسی ندارید")
    inbound_id = int(r["three_xui_inbound_id"])
    client_id = r["three_xui_client_id"]
    try:
        new_subid = await three_session.rotate_subid(inbound_id, client_id)
        new_link = build_subscribe_url(new_subid)
        cur.execute("UPDATE purchases SET sub_id=?, sub_link=? WHERE id=?", (new_subid, new_link, pid))
        await cb.bot.send_message(cb.from_user.id, f"لینک جدید:\n<a href=\"{htmlesc(new_link)}\">باز کردن لینک اشتراک</a>\n<code>{new_link}</code>", parse_mode=ParseMode.HTML)
        await cb.answer("لینک جدید ارسال شد ✅")
    except Exception as e:
        msg = str(e)
        msg = (msg[:180] + "…") if len(msg) > 180 else msg
        await cb.answer(f"خطا: {msg}", show_alert=True)


@router.callback_query(F.data.startswith("substat:"))
async def sub_stat_refresh(cb: CallbackQuery):
    if not three_session:
        return await cb.answer("اتصال به پنل تنظیم نشده", show_alert=True)
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("اجازه دسترسی ندارید")
    inbound_id = int(r["three_xui_inbound_id"])
    client_id = r["three_xui_client_id"]
    stat = await three_session.get_client_stats(inbound_id, client_id, r["client_email"])
    if not stat:
        return await cb.answer("اطلاعات در دسترس نیست", show_alert=True)
    total = int(stat.get("total") or 0)
    if total <= 0 and int(r["allocated_gb"] or 0) > 0:
        total = int(r["allocated_gb"]) * 1024 ** 3
    expiry = int(stat.get("expiryTime") or r["expiry_ms"] or 0)
    from db import cache_set_usage

    cache_set_usage(pid, int(stat.get("up") or 0), int(stat.get("down") or 0), total, expiry)
    await cb.answer("به‌روزرسانی شد ✅")
    await sub_detail(cb)






@router.callback_query(F.data == "recheck_join")
async def recheck_join(cb: CallbackQuery):
    # Re-evaluate membership and route to home or show prompt
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    # Reuse existing welcome/home flow
    from db import get_setting
    from keyboards import kb_main, kb_force_join
    from config import REQUIRED_CHANNEL
    try:
        # Try to show home if passed middleware
        bal = db_get_wallet(cb.from_user.id)
        welcome = get_setting("WELCOME_TEMPLATE", "به پینگ‌اِکس خوش آمدید!")
        await cb.message.edit_text(
            welcome + f"\n\nموجودی شما: <b>{bal:,}</b> تومان",
            reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await cb.message.edit_text(
            "برای استفاده از ربات، ابتدا در کانال عضو شوید.",
            reply_markup=kb_force_join(get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL)),
        )


@router.message()
async def fallback_main_menu(m: Message, state: FSMContext):
    # Private only
    if getattr(m.chat, "type", "private") != "private":
        return
    # Skip commands
    if m.text and str(m.text).startswith("/"):
        return
    # Skip if in FSM states (e.g., Topup)
    s = await state.get_state()
    if s:
        return
    # Skip if user has open ticket (tickets router will handle)
    row = cur.execute("SELECT 1 FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1", (m.from_user.id,)).fetchone()
    if row:
        return
    # Show main menu
    bal = db_get_wallet(m.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "به پینگ‌اِکس خوش آمدید!")
    await m.answer(
        welcome + f"\n\nموجودی شما: <b>{bal:,}</b> تومان",
        reply_markup=kb_main(m.from_user.id, is_admin(m.from_user.id)),
        parse_mode=ParseMode.HTML,
    )
