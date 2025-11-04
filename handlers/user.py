from aiogram import Router, F
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from db import is_admin, get_admin_ids
from config import THREEXUI_INBOUND_ID, SUB_PATH, SUB_PORT, SUB_SCHEME, SUB_HOST
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
    host = SUB_HOST or (three_session and three_session.base.split("://")[-1].split(":")[0]) or "localhost"
    path = SUB_PATH if SUB_PATH.endswith("/") else (SUB_PATH + "/")
    return f"{SUB_SCHEME}://{host}:{SUB_PORT}{path}{sub_id}"


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
        await m.answer("ظ„ط·ظپط§ ط§ط¨طھط¯ط§ ط¯ط± ع©ط§ظ†ط§ظ„ ط¹ط¶ظˆ ط´ظˆغŒط¯.", reply_markup=kb_force_join(get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL)))
        return
    bal = db_get_wallet(m.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "ط¨ظ‡ ظ¾غŒظ†ع¯â€Œط§ظگع©ط³ ط®ظˆط´ ط¢ظ…ط¯غŒط¯!")
    await m.answer(welcome + f"\n\nظ…ظˆط¬ظˆط¯غŒ ط´ظ…ط§: <b>{bal:,}</b>", reply_markup=kb_main(m.from_user.id, is_admin(m.from_user.id)))


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    if getattr(cb.message.chat, "type", "private") != "private":
        return
    bal = db_get_wallet(cb.from_user.id)
    welcome = get_setting("WELCOME_TEMPLATE", "ط¨ظ‡ ظ¾غŒظ†ع¯â€Œط§ظگع©ط³ ط®ظˆط´ ط¢ظ…ط¯غŒط¯!")
    await cb.message.edit_text(welcome + f"\n\ظ†ظ…ظˆط¬ظˆط¯غŒ ط´ظ…ط§: <b>{bal:,}</b>", reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)))


@router.callback_query(F.data == "buy")
async def buy_menu(cb: CallbackQuery):
    plans = db_get_plans_for_user(is_admin(cb.from_user.id))
    await cb.message.edit_text("ظ„ط·ظپط§ غŒع© ظ¾ظ„ظ† ط¨ط±ط§غŒ ط®ط±غŒط¯ ط§ظ†طھط®ط§ط¨ ع©ظ†غŒط¯:", reply_markup=kb_plans(plans, is_admin(cb.from_user.id)))


@router.callback_query(F.data.startswith("plan:"))
async def plan_select(cb: CallbackQuery):
    pid = cb.data.split(":")[1]
    plan = db_get_plan(pid)
    if not plan:
        return await cb.answer("ظ¾ظ„ظ† ظ¾غŒط¯ط§ ظ†ط´ط¯")
    price = int(plan["price"])
    bal = db_get_wallet(cb.from_user.id)
    if bal < price:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="ط§ظپط²ط§غŒط´ ظ…ظˆط¬ظˆط¯غŒ", callback_data="topup")],
                [InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="buy")],
            ]
        )
        await cb.message.edit_text(
            f"ظ…ظˆط¬ظˆط¯غŒ ع©ط§ظپغŒ ظ†غŒط³طھ. ظ‚غŒظ…طھ: <b>{price:,}</b> آ· ظ…ظˆط¬ظˆط¯غŒ ط´ظ…ط§: <b>{bal:,}</b>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="طھط§غŒغŒط¯ ط®ط±غŒط¯ âœ…", callback_data=f"confirm:{pid}")],
            [InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="buy")],
        ]
    )
    await cb.message.edit_text(
        f"ظ¾ظ„ظ† ط§ظ†طھط®ط§ط¨â€Œط´ط¯ظ‡: <b>{plan['title']}</b> آ· ظ‚غŒظ…طھ: <b>{price:,}</b>", reply_markup=kb, parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("confirm:"))
async def buy_confirm(cb: CallbackQuery):
    pid = cb.data.split(":")[1]
    plan = db_get_plan(pid)
    if not plan:
        return await cb.answer("ظ¾ظ„ظ† ظ¾غŒط¯ط§ ظ†ط´ط¯")
    price = int(plan["price"])
    if not try_deduct_wallet(cb.from_user.id, price):
        return await cb.answer("ظ…ظˆط¬ظˆط¯غŒ ع©ط§ظپغŒ ظ†غŒط³طھ")
    if not three_session:
        rollback_wallet(cb.from_user.id, price)
        await cb.message.edit_text("ط§طھطµط§ظ„ ط¨ظ‡ ظ¾ظ†ظ„ طھظ†ط¸غŒظ… ظ†ط´ط¯ظ‡ ط§ط³طھ. ع©ظ…غŒ ط¨ط¹ط¯ ط¯ظˆط¨ط§ط±ظ‡ طھظ„ط§ط´ ع©ظ†غŒط¯.")
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
        await cb.message.edit_text(f"ط®ط·ط§ ط¯ط± ط§غŒط¬ط§ط¯ ط§ط´طھط±ط§ع©:\n<code>{htmlesc(str(e))}</code>", parse_mode=ParseMode.HTML)
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
            caption="QR ع©ط¯ غŒط§ ظ„غŒظ†ع© ط²غŒط± ط±ط§ ط§ط³طھظپط§ط¯ظ‡ ع©ظ†غŒط¯.",
        )
        await cb.bot.send_message(
            cb.from_user.id,
            f"ًں”— <a href=\"{htmlesc(sub_link)}\">ط¨ط§ط² ع©ط±ط¯ظ† ظ„غŒظ†ع© ط§ط´طھط±ط§ع©</a>\n<code>{sub_link}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    extra = get_setting("POST_PURCHASE_TEMPLATE", "").strip()
    if extra:
        await cb.bot.send_message(cb.from_user.id, extra)
    log_evt(cb.from_user.id, "purchase_confirm", {"purchase_id": pid2, "plan_id": plan["id"], "inbound_id": inbound_id})
    await cb.message.edit_text("ط®ط±غŒط¯ ط¨ط§ ظ…ظˆظپظ‚غŒطھ ط§ظ†ط¬ط§ظ… ط´ط¯.", reply_markup=kb_main(cb.from_user.id, is_admin(cb.from_user.id)))


@router.callback_query(F.data == "mysubs")
async def mysubs(cb: CallbackQuery):
    rows = user_purchases(cb.from_user.id)
    if not rows:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="ط®ط±غŒط¯ ط§ط´طھط±ط§ع© ًں›’", callback_data="buy")],
                [InlineKeyboardButton(text="ط¨ط§ط²ع¯ط´طھ â¬…ï¸ڈ", callback_data="home")],
            ]
        )
        await cb.message.edit_text("ظ‡غŒع† ط§ط´طھط±ط§ع©غŒ غŒط§ظپطھ ظ†ط´ط¯.", reply_markup=kb)
        return
    await cb.message.edit_text("ط§ط´طھط±ط§ع©â€Œظ‡ط§غŒ ط´ظ…ط§:", reply_markup=kb_mysubs(rows))


@router.callback_query(F.data.startswith("sub:"))
async def sub_detail(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("ط§ط¬ط§ط²ظ‡ ط¯ط³طھط±ط³غŒ ظ†ط¯ط§ط±غŒط¯")
    cached = cache_get_usage(pid)
    usage_txt = "ط§ط·ظ„ط§ط¹ط§طھغŒ ط§ط² ظ…طµط±ظپ ظ…ظˆط¬ظˆط¯ ظ†غŒط³طھ. ط±ظˆغŒ ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ط¨ط²ظ†غŒط¯."
    if cached:
        up = int(cached.get("up") or 0)
        down = int(cached.get("down") or 0)
        total = int(cached.get("total") or 0)
        used = up + down
        pct = 0.0 if total <= 0 else min(1.0, used / total)
        bar = progress_bar(pct)
        total_hr = "ظ†ط§ظ…ط­ط¯ظˆط¯" if total <= 0 else human_bytes(total)
        exp_ts = int((cached.get("expiry_ms") or r["expiry_ms"] or 0) / 1000)
        exp_txt = datetime.fromtimestamp(exp_ts, TZ).strftime('%Y-%m-%d %H:%M') if exp_ts else "-"
        usage_txt = f"ظ…طµط±ظپ: {human_bytes(used)} / {total_hr} ({int(pct*100)}%)\n{bar}\nط§ظ†ظ‚ط¶ط§: {exp_txt}"
    text = (
        f"<b>ط§ط´طھط±ط§ع© #{r['id']}</b>\nظ¾ظ„ظ†: {r['plan_id']} | ظ‚غŒظ…طھ: {r['price']:,}\n"
        f"Inbound: {r['three_xui_inbound_id']}\nClient: <code>{r['three_xui_client_id']}</code>\n"
        f"SubId: <code>{r['sub_id'] or '-'}</code>\n\n{usage_txt}"
    )
    await cb.message.edit_text(text, reply_markup=kb_sub_detail(pid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("subfix:"))
async def sub_fix_link(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("ط§ط¬ط§ط²ظ‡ ط¯ط³طھط±ط³غŒ ظ†ط¯ط§ط±غŒط¯")
    link = build_subscribe_url(r["sub_id"]) if r["sub_id"] else r["sub_link"]
    try:
        await cb.bot.send_photo(
            cb.from_user.id,
            BufferedInputFile(qr_bytes(link).getvalue(), filename=f"pingx-{pid}.png"),
            caption="ع©ط¯ QR غŒط§ ظ„غŒظ†ع©:",
        )
    except Exception:
        pass
    await cb.bot.send_message(cb.from_user.id, f"<a href=\"{htmlesc(link)}\">ط¨ط§ط² ع©ط±ط¯ظ† ظ„غŒظ†ع© ط§ط´طھط±ط§ع©</a>\n<code>{link}</code>", parse_mode=ParseMode.HTML)
    await cb.answer("ط§ط±ط³ط§ظ„ ط´ط¯")


@router.callback_query(F.data.startswith("subrevoke:"))
async def sub_revoke(cb: CallbackQuery):
    if not three_session:
        return await cb.answer("ط§طھطµط§ظ„ ط¨ظ‡ ظ¾ظ†ظ„ طھظ†ط¸غŒظ… ظ†ط´ط¯ظ‡", show_alert=True)
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("ط§ط¬ط§ط²ظ‡ ط¯ط³طھط±ط³غŒ ظ†ط¯ط§ط±غŒط¯")
    inbound_id = int(r["three_xui_inbound_id"])
    client_id = r["three_xui_client_id"]
    try:
        new_subid = await three_session.rotate_subid(inbound_id, client_id)
        new_link = build_subscribe_url(new_subid)
        cur.execute("UPDATE purchases SET sub_id=?, sub_link=? WHERE id=?", (new_subid, new_link, pid))
        await cb.bot.send_message(cb.from_user.id, f"ظ„غŒظ†ع© ط¬ط¯غŒط¯:\n<a href=\"{htmlesc(new_link)}\">ط¨ط§ط² ع©ط±ط¯ظ† ظ„غŒظ†ع© ط§ط´طھط±ط§ع©</a>\n<code>{new_link}</code>", parse_mode=ParseMode.HTML)
        await cb.answer("ط§ظ†ط¬ط§ظ… ط´ط¯")
    except Exception as e:
        msg = str(e)
        msg = (msg[:180] + "â€¦") if len(msg) > 180 else msg
        await cb.answer(f"ط®ط·ط§: {msg}", show_alert=True)


@router.callback_query(F.data.startswith("substat:"))
async def sub_stat_refresh(cb: CallbackQuery):
    if not three_session:
        return await cb.answer("ط§طھطµط§ظ„ ط¨ظ‡ ظ¾ظ†ظ„ طھظ†ط¸غŒظ… ظ†ط´ط¯ظ‡", show_alert=True)
    pid = int(cb.data.split(":")[1])
    r = cur.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != cb.from_user.id:
        return await cb.answer("ط§ط¬ط§ط²ظ‡ ط¯ط³طھط±ط³غŒ ظ†ط¯ط§ط±غŒط¯")
    inbound_id = int(r["three_xui_inbound_id"])
    client_id = r["three_xui_client_id"]
    stat = await three_session.get_client_stats(inbound_id, client_id, r["client_email"])
    if not stat:
        return await cb.answer("ط§ط·ظ„ط§ط¹ط§طھ ط¯ط± ط¯ط³طھط±ط³ ظ†غŒط³طھ", show_alert=True)
    total = int(stat.get("total") or 0)
    if total <= 0 and int(r["allocated_gb"] or 0) > 0:
        total = int(r["allocated_gb"]) * 1024 ** 3
    expiry = int(stat.get("expiryTime") or r["expiry_ms"] or 0)
    from db import cache_set_usage

    cache_set_usage(pid, int(stat.get("up") or 0), int(stat.get("down") or 0), total, expiry)
    await cb.answer("ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ط´ط¯"); await sub_detail(cb)




