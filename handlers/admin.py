import re, json, secrets
import os, sqlite3, tempfile, zipfile, shutil
from datetime import datetime, timedelta
from pathlib import Path
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from db import is_admin, get_admin_ids, conn, is_support, is_staff
from keyboards import kb_admin_root
from db import (
    cur,
    get_setting,
    set_setting,
    db_get_plan,
    db_get_plans_for_user,
    user_purchases,
    cache_get_usage,
    db_get_wallet,
    db_add_wallet,
    log_evt,
    db_list_plans,
    db_insert_plan,
    db_update_plan_field,
    db_delete_plan,
    db_swap_plan_order,
    create_referral,
    list_referrals,
    get_referral,
    update_referral_title,
    update_referral_description,
    list_referral_joiners,
    get_support_ids,
    add_support,
    remove_support,
    count_users,
    purchases_stats_range,
    events_count,
    get_global_discount_percent,
)
from utils import htmlesc, human_bytes, parse_channel_list, TZ, format_toman
from xui import three_session
from config import THREEXUI_INBOUND_ID, PAGE_SIZE_USERS, DB_PATH

router = Router()


SETTINGS_MENU = [
    ("CARD_NUMBER", "شماره کارت"),
    ("REQUIRED_CHANNELS", "کانال‌های اجباری"),
]

SETTINGS_KEYS_PATTERN = "|".join(key for key, _ in SETTINGS_MENU)


SETTINGS_META = {
    "REQUIRED_CHANNELS": {"type": "channels"},
}


class RefEdit(StatesGroup):
    title = State()
    description = State()

class BackupRestore(StatesGroup):
    restore_waiting = State()


class SupportAdd(StatesGroup):
    waiting = State()


class DiscountEdit(StatesGroup):
    waiting = State()


class Broadcast(StatesGroup):
    waiting = State()


def _generate_ref_code():
    import secrets

    while True:
        code = secrets.token_hex(4)
        if not cur.execute("SELECT 1 FROM referral_links WHERE code=?", (code,)).fetchone():
            return code


def _create_backup_archive() -> tuple[Path, str]:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmpdir = tempfile.mkdtemp(prefix="pingx-backup-")
    tmp_path = Path(tmpdir)
    db_copy = tmp_path / f"bot-{ts}.db"
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as src, sqlite3.connect(db_copy) as dst:
        src.backup(dst)
    settings_file = tmp_path / "settings.json"
    settings_rows = [dict(r) for r in cur.execute("SELECT key,value FROM settings").fetchall()]
    with settings_file.open("w", encoding="utf-8") as fh:
        json.dump(settings_rows, fh, ensure_ascii=False, indent=2)
    zip_path = tmp_path / f"pingx-backup-{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, arcname="bot.db")
        zf.write(settings_file, arcname="settings.json")
    return zip_path, tmpdir


def _backup_live_db() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    dst_path = backup_dir / f"before-restore-{ts}.db"
    with sqlite3.connect(dst_path) as dst:
        conn.backup(dst)
    return dst_path


def _restore_into_live(db_source: Path):
    with sqlite3.connect(db_source) as src:
        src.backup(conn)
    conn.commit()


@router.callback_query(F.data == "admin:backup")
async def admin_backup(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await cb.answer("در حال تهیه بکاپ...")
    zip_path = None
    tmpdir = None
    try:
        zip_path, tmpdir = _create_backup_archive()
        await cb.message.answer_document(
            FSInputFile(zip_path),
            caption="Backup آماده شد. فایل zip را نگه دارید.",
        )
    except Exception as e:
        await cb.message.answer(f"خطا در بکاپ: {e}")
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


@router.callback_query(F.data == "admin:restore")
async def admin_restore(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data="admin:restore:cancel")]])
    await state.set_state(BackupRestore.restore_waiting)
    await cb.message.edit_text(
        "فایل بکاپ (.zip یا .db) را ارسال کنید. پیش از ریستور یک کپی ایمن از دیتابیس فعلی گرفته می‌شود.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "admin:restore:cancel")
async def admin_restore_cancel(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.clear()
    await cb.message.edit_text("پنل ادمین:", reply_markup=kb_admin_root(is_admin=True))


@router.message(StateFilter(BackupRestore.restore_waiting))
async def admin_restore_file(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("دسترسی غیرمجاز")
        return await state.clear()
    if not m.document:
        return await m.answer("یک فایل بکاپ .zip یا .db ارسال کنید.")
    size_limit = 50 * 1024 * 1024
    if m.document.file_size and m.document.file_size > size_limit:
        return await m.answer("حجم فایل زیاد است. حداکثر 50MB.")

    tmpdir = tempfile.mkdtemp(prefix="pingx-restore-")
    tmp_path = Path(tmpdir)
    fname = m.document.file_name or "restore.bin"
    dest = tmp_path / fname
    try:
        await m.document.download(destination=dest)
        db_source = None
        if dest.suffix.lower() == ".zip":
            with zipfile.ZipFile(dest, "r") as zf:
                db_member = next((n for n in zf.namelist() if n.lower().endswith(".db")), None)
                if not db_member:
                    return await m.answer("در فایل zip هیچ دیتابیس (.db) پیدا نشد.")
                zf.extract(db_member, tmp_path)
                db_source = (tmp_path / db_member).resolve()
        elif dest.suffix.lower() == ".db":
            db_source = dest
        else:
            return await m.answer("فرمت پشتیبانی نمی‌شود. فقط .zip یا .db.")

        if not db_source or not db_source.exists():
            return await m.answer("فایل دیتابیس پیدا نشد.")

        safety = _backup_live_db()
        _restore_into_live(db_source)
        await m.answer(f"ریستور انجام شد. نسخه فعلی قبل از ریستور در {safety} ذخیره شد.", reply_markup=kb_admin_root(is_admin=True))
        await state.clear()
    except Exception as e:
        await m.answer(f"ریستور با خطا مواجه شد: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def kb_admin_refs(rows):
    kb = []
    for r in rows[:10]:
        code = r["code"]
        kb.append([InlineKeyboardButton(text=f"{r['title'] or code} | کلیک {r['clicks']} | ثبت {r['signups']}", callback_data=f"admin:ref:{code}")])
    kb.append([InlineKeyboardButton(text="➕ ساخت لینک جدید", callback_data="admin:refs:new")])
    kb.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data == "admin")
async def admin_menu(cb: CallbackQuery):
    if not is_staff(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    is_adm = is_admin(cb.from_user.id)
    is_sup = is_support(cb.from_user.id)
    title = "پنل ادمین:" if is_adm else "پنل پشتیبان:"
    await cb.message.edit_text(title, reply_markup=kb_admin_root(is_admin=is_adm, is_support=is_sup))


def kb_supports(ids):
    kb = [[InlineKeyboardButton(text=str(sid), callback_data=f"admin:supports:del:{sid}")] for sid in sorted(ids)]
    kb.append([InlineKeyboardButton(text="➕ افزودن پشتیبان", callback_data="admin:supports:add")])
    kb.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data == "admin:supports")
async def admin_supports(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state():
        await state.clear()
    ids = get_support_ids()
    text = "🧑‍💻 پشتیبان‌های فعلی:\n" + ("\n".join(str(x) for x in sorted(ids)) if ids else "هنوز کسی اضافه نشده است.")
    await cb.message.edit_text(text, reply_markup=kb_supports(ids))


@router.callback_query(F.data == "admin:supports:add")
async def admin_supports_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(SupportAdd.waiting)
    await cb.message.edit_text(
        "آیدی عددی کاربر را ارسال کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:supports")]]),
    )


@router.message(StateFilter(SupportAdd.waiting))
async def admin_supports_add_recv(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("دسترسی غیرمجاز")
        return await state.clear()
    try:
        uid = int((m.text or "").strip())
        if uid <= 0:
            raise ValueError("invalid")
    except Exception:
        return await m.reply("آیدی معتبر نیست.")
    add_support(uid)
    await state.clear()
    await m.reply(f"پشتیبان {uid} اضافه شد.", reply_markup=kb_supports(get_support_ids()))


@router.callback_query(F.data.regexp(r"^admin:supports:del:(\d+)$"))
async def admin_supports_del(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:supports:del:(\d+)$", cb.data).group(1))
    remove_support(uid)
    ids = get_support_ids()
    text = "🧑‍💻 پشتیبان‌های فعلی:\n" + ("\n".join(str(x) for x in sorted(ids)) if ids else "هنوز کسی اضافه نشده است.")
    await cb.message.edit_text(text, reply_markup=kb_supports(ids))


def kb_reports():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="امروز", callback_data="admin:reports:1")],
            [InlineKeyboardButton(text="۷ روز اخیر", callback_data="admin:reports:7")],
            [InlineKeyboardButton(text="۳۰ روز اخیر", callback_data="admin:reports:30")],
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")],
        ]
    )


def _report_range_bounds(days: int):
    now = datetime.now(TZ)
    if days <= 1:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


@router.callback_query(F.data == "admin:reports")
async def admin_reports(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await cb.message.edit_text("بازه گزارش را انتخاب کنید:", reply_markup=kb_reports())


@router.callback_query(F.data.regexp(r"^admin:reports:(1|7|30)$"))
async def admin_reports_range(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    days = int(re.match(r"^admin:reports:(1|7|30)$", cb.data).group(1))
    start, end = _report_range_bounds(days)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    stats = purchases_stats_range(start_iso, end_iso)
    revenue = stats.get("revenue", 0)
    orders = stats.get("orders", 0)
    buyers = stats.get("buyers", 0)
    aov = (revenue / orders) if orders else 0
    total_users = count_users()
    conversion = (buyers / total_users * 100) if total_users else 0
    checkout = events_count("checkout_initiated", start_iso, end_iso)
    success = events_count("purchase_success", start_iso, end_iso)
    funnel = (success / checkout * 100) if checkout else 0
    labels = {1: "امروز", 7: "۷ روز اخیر", 30: "۳۰ روز اخیر"}
    lines = [
        f"🗓 بازه: {labels.get(days, days)}",
        f"💰 درآمد: {format_toman(revenue)}",
        f"🧾 سفارشات: {orders:,}",
        f"🛍 خریداران یونیک: {buyers:,}",
        f"💳 AOV: {format_toman(int(aov))}",
        f"🎯 کانورژن ساده: {conversion:.1f}%",
        f"📊 قیف خرید: {funnel:.1f}% (purchase_success / checkout_initiated)",
        f"رویدادها: checkout_initiated={checkout:,} | purchase_success={success:,}",
    ]
    await cb.message.edit_text("\n".join(lines), reply_markup=kb_reports())


@router.callback_query(F.data == "admin:refs")
async def admin_refs(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    rows = list_referrals()
    bot_un = getattr(cb.bot, "username", None) or "yourbot"
    text_lines = ["📈 لینک‌های رفرال (روی هر کد بزن تا جزئیات را ببینی):"]
    for r in rows[:10]:
        code = r["code"]
        link = f"https://t.me/{bot_un}?start=ref-{code}"
        clicks = int(r.get("clicks") or 0)
        signups = int(r.get("signups") or 0)
        conv = (signups / clicks * 100) if clicks else 0
        desc = (r.get("description") or "").strip()
        desc_snippet = f" — {desc[:40]}{'…' if len(desc) > 40 else ''}" if desc else ""
        text_lines.append(
            f"{r['title'] or code}: کلیک {clicks} | ثبت {signups} | تبدیل {conv:.1f}%{desc_snippet}\n{link}"
        )
    if not rows:
        text_lines.append("فعلاً لینکی ساخته نشده است.")
    await cb.message.edit_text("\n".join(text_lines), reply_markup=kb_admin_refs(rows))


def _build_ref_detail(bot_un: str, code: str):
    r = get_referral(code)
    if not r:
        return None, None
    link = f"https://t.me/{bot_un}?start=ref-{code}"
    clicks = int(r.get("clicks") or 0)
    signups = int(r.get("signups") or 0)
    conv = (signups / clicks * 100) if clicks else 0
    desc = (r.get("description") or "").strip() or "—"
    created = (r.get("created_at") or "").replace("T", " ")[:19]
    joiners = list_referral_joiners(code, limit=8)
    lines = [
        f"📌 <b>{htmlesc(r['title'] or code)}</b>",
        f"کد: <code>{htmlesc(code)}</code>",
        f"لینک: <a href=\"{htmlesc(link)}\">{htmlesc(link)}</a>",
        f"توضیح: {htmlesc(desc)}",
        f"کلیک: {clicks:,} | ثبت: {signups:,} | تبدیل: {conv:.1f}%",
        f"ساخته شده: {htmlesc(created)}",
    ]
    if joiners:
        lines.append("")
        lines.append("آخرین عضوها:")
        for j in joiners:
            uname = j.get("username") or ""
            name = (j.get("first_name") or "").strip() or "-"
            label = f"@{uname}" if uname else name
            joined = (j.get("joined_at") or "").replace("T", " ")[:16]
            lines.append(f"• {htmlesc(label)} ({j.get('user_id')}) | {htmlesc(joined)}")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ ویرایش عنوان", callback_data=f"admin:ref:settitle:{code}")],
            [InlineKeyboardButton(text="📝 ویرایش توضیح", callback_data=f"admin:ref:setdesc:{code}")],
            [InlineKeyboardButton(text="⬅️ فهرست لینک‌ها", callback_data="admin:refs")],
        ]
    )
    return text, kb


async def _show_ref_detail(cb_or_msg, bot_un: str, code: str):
    text, kb = _build_ref_detail(bot_un, code)
    if not text:
        if isinstance(cb_or_msg, CallbackQuery):
            return await cb_or_msg.answer("کد پیدا نشد.", show_alert=True)
        return await cb_or_msg.answer("کد پیدا نشد.")
    if isinstance(cb_or_msg, CallbackQuery):
        await cb_or_msg.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await cb_or_msg.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.callback_query(F.data.regexp(r"^admin:ref:([a-zA-Z0-9]+)$"))
async def admin_ref_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    code = cb.data.split(":", 2)[2]
    await _show_ref_detail(cb, getattr(cb.bot, "username", None) or "yourbot", code)


@router.callback_query(F.data == "admin:refs:new")
async def admin_refs_new(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    code = _generate_ref_code()
    title = f"ref-{code}"
    create_referral(code, title, cb.from_user.id)
    await cb.answer("لینک جدید ساخته شد.")
    await admin_refs(cb)


@router.callback_query(F.data.regexp(r"^admin:ref:settitle:(.+)$"))
async def admin_ref_set_title(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    code = cb.data.split(":", 3)[3]
    await state.set_state(RefEdit.title)
    await state.update_data(code=code)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"admin:ref:{code}")]])
    await cb.message.edit_text(f"عنوان جدید برای {code} را بفرستید:", reply_markup=kb)


@router.callback_query(F.data.regexp(r"^admin:ref:setdesc:(.+)$"))
async def admin_ref_set_desc(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    code = cb.data.split(":", 3)[3]
    await state.set_state(RefEdit.description)
    await state.update_data(code=code)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"admin:ref:{code}")]])
    await cb.message.edit_text(f"توضیح دلخواه برای {code} را بفرستید:", reply_markup=kb)


@router.message(StateFilter(RefEdit.title))
async def admin_ref_save_title(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("دسترسی غیرمجاز")
        return await state.clear()
    data = await state.get_data()
    code = data.get("code")
    new_title = (m.text or "").strip()
    if not code:
        await m.answer("کد پیدا نشد.")
        return await state.clear()
    if not new_title:
        return await m.answer("عنوان نباید خالی باشد.")
    update_referral_title(code, new_title[:64])
    await m.answer("عنوان به‌روزرسانی شد.")
    await _show_ref_detail(m, getattr(m.bot, "username", None) or "yourbot", code)
    await state.clear()


@router.message(StateFilter(RefEdit.description))
async def admin_ref_save_desc(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("دسترسی غیرمجاز")
        return await state.clear()
    data = await state.get_data()
    code = data.get("code")
    new_desc = (m.text or "").strip()
    if not code:
        await m.answer("کد پیدا نشد.")
        return await state.clear()
    update_referral_description(code, new_desc[:240])
    await m.answer("توضیح به‌روزرسانی شد.")
    await _show_ref_detail(m, getattr(m.bot, "username", None) or "yourbot", code)
    await state.clear()


def search_users_page(q: str, offset: int, limit: int):
    ql = f"%{q.lower()}%"
    rows = cur.execute(
        """
    SELECT user_id,username,first_name,last_name,wallet,created_at FROM users
    WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(first_name,'')) LIKE ?
       OR lower(COALESCE(last_name,'')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    ORDER BY created_at DESC LIMIT ? OFFSET ?
    """,
        (ql, ql, ql, ql, limit, offset),
    ).fetchall()
    total = cur.execute(
        """
    SELECT COUNT(1) FROM users
    WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(first_name,'')) LIKE ?
       OR lower(COALESCE(last_name,'')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    """,
        (ql, ql, ql, ql),
    ).fetchone()[0]
    return [dict(r) for r in rows], total


def list_users_page(offset: int, limit: int):
    rows = cur.execute(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    total = cur.execute("SELECT COUNT(1) FROM users").fetchone()[0]
    return [dict(r) for r in rows], total


def kb_admin_users_list(rows, page: int, total: int, page_size: int, q: str | None = None):
    kb = []
    for r in rows:
        name = (
            " ".join(
                filter(
                    None,
                    [r["first_name"] or "", r["last_name"] or ""],
                )
            )
            or (r["username"] or str(r["user_id"]))
        ).strip()
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{name} ({r['user_id']}) · {format_toman(r['wallet'])}",
                    callback_data=f"admin:u:{r['user_id']}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="قبلی", callback_data=f"admin:users:{page-1}:{q or ''}"
            )
        )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="بعدی", callback_data=f"admin:users:{page+1}:{q or ''}"
            )
        )
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.regexp(r"^admin:users:(\d+):(.*)$"))
async def admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin:users:(\d+):(.*)$", cb.data)
    page = int(m.group(1))
    q = (m.group(2) or "").strip()
    limit = PAGE_SIZE_USERS
    offset = page * limit
    if q:
        rows, total = search_users_page(q, offset, limit)
        header = f"کاربران (جستجو: {htmlesc(q)}):"
    else:
        rows, total = list_users_page(offset, limit)
        header = "کاربران:"
    await cb.message.edit_text(
        header, reply_markup=kb_admin_users_list(rows, page, total, limit, q)
    )


async def _render_admin_user_detail(cb: CallbackQuery, uid: int):
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not u:
        await cb.answer("کاربر یافت نشد", show_alert=True)
        return False
    text = (
        f"<b>کاربر {uid}</b>\n"
        f"نام: {(u['first_name'] or '').strip()} {(u['last_name'] or '').strip()}\n"
        f"یوزرنیم: @{u['username'] or '-'}\n"
        f"موجودی فعلی: {format_toman(u['wallet'])}\n"
        f"تاریخ عضویت: {u['created_at'][:19].replace('T',' ')}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="خریدها", callback_data=f"admin:u:buys:{uid}")],
            [InlineKeyboardButton(text="مصرف", callback_data=f"admin:u:usage:{uid}")],
            [InlineKeyboardButton(text="اعطای آزمایشی ۷روزه", callback_data=f"admin:u:trial7:{uid}")],
            [
                InlineKeyboardButton(text="+50k", callback_data=f"admin:u:wallet:{uid}:+50000"),
                InlineKeyboardButton(text="-50k", callback_data=f"admin:u:wallet:{uid}:-50000"),
            ],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return True


@router.callback_query(F.data.regexp(r"^admin:u:(\d+)$"))
async def admin_user_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:(\d+)$", cb.data).group(1))
    await _render_admin_user_detail(cb, uid)


@router.callback_query(F.data.regexp(r"^admin:u:wallet:(\d+):([+-]?\d+)$"))
async def admin_user_wallet_adjust(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin:u:wallet:(\d+):([+-]?\d+)$", cb.data)
    uid = int(m.group(1))
    delta = int(m.group(2))
    u = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not u:
        return await cb.answer("کاربر یافت نشد", show_alert=True)
    db_add_wallet(uid, delta)
    log_evt(cb.from_user.id, "wallet_adjust", {"target": uid, "delta": delta})
    await cb.answer(f"تغییر {delta:+,} ثبت شد.")
    await _render_admin_user_detail(cb, uid)




@router.callback_query(F.data.regexp(r"^admin:u:buys:(\d+)$"))
async def admin_user_buys(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:buys:(\d+)$", cb.data).group(1))
    rows = user_purchases(uid)
    if not rows:
        text = "خریدی برای این کاربر ثبت نشده است."
    else:
        lines = []
        for r in rows[:10]:
            ts = r.get("created_at") or ""
            ts = ts[:19].replace("T", " ") if ts else "-"
            lines.append(
                f"#{r['id']} | پلن {htmlesc(r['plan_id'])} | مبلغ {format_toman(r['price'])} | تاریخ {ts}"
            )
        text = "<b>خریدهای اخیر:</b>\n" + "\n".join(lines)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بازگشت به کاربر", callback_data=f"admin:u:{uid}")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin:u:usage:(\d+)$"))
async def admin_user_usage(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    uid = int(re.match(r"^admin:u:usage:(\d+)$", cb.data).group(1))
    rows = user_purchases(uid)
    if not rows:
        text = "مصرفی برای این کاربر یافت نشد."
    else:
        lines = []
        for r in rows[:5]:
            cached = cache_get_usage(r["id"])
            if cached:
                used = int(cached.get("up") or 0) + int(cached.get("down") or 0)
                total = int(cached.get("total") or 0)
                pct = 0 if total <= 0 else min(99, int((used / total) * 100))
                total_hr = "نامحدود" if total <= 0 else human_bytes(total)
                usage_txt = f"{human_bytes(used)} / {total_hr} ({pct}٪)"
            else:
                usage_txt = "داده‌ای ثبت نشده"
            lines.append(f"#{r['id']} | پلن {htmlesc(r['plan_id'])} | {usage_txt}")
        text = "<b>وضعیت مصرف:</b>\n" + "\n".join(lines)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بازگشت به کاربر", callback_data=f"admin:u:{uid}")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin:users:0:")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- Plans management ---


class PlanNew(StatesGroup):
    waiting = State()


class PlanEdit(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:plans")
async def admin_plans_stub_redirect(cb: CallbackQuery, state: FSMContext):
    # Backward-compatibility if keyboard still points here
    return await admin_plans(cb, state)


@router.callback_query(F.data == "admin2:plans")
async def admin_plans(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    curr_state = await state.get_state()
    if curr_state in {PlanNew.waiting.state, PlanEdit.waiting.state}:
        await state.clear()
    plans = db_list_plans()
    kb = []
    for p in plans:
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"[{p.get('sort_order')}] {p['id']} | {p['title']} | {format_toman(p['price'])}",
                    callback_data=f"admin2:plan:{p['id']}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="Add Plan", callback_data="admin2:plan:add")])
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    await cb.message.edit_text("Plans:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "admin2:plan:add")
async def admin_plan_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(PlanNew.waiting)
    await cb.message.edit_text(
        "Send new plan as: id | title | days | gb | price",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:plans")]]
        ),
    )


@router.message(StateFilter(PlanNew.waiting))
async def admin_plan_add_recv(m: Message, state: FSMContext):
    if m.text and m.text.strip().lower() == "/cancel":
        await state.clear()
        return await m.reply("Cancelled.")
    try:
        parts = [x.strip() for x in (m.text or "").split("|")]
        if len(parts) != 5:
            raise ValueError("need 5 parts (id|title|days|gb|price)")
        pid, title, days, gb, price = parts
        days = int(days)
        gb = int(gb)
        price = int(price)
        db_insert_plan(pid, title, days, gb, price)
        await state.clear()
        await m.reply(
            "Plan added.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت به لیست پلن‌ها", callback_data="admin2:plans")]]
            ),
        )
    except Exception as e:
        return await m.reply(f"Invalid input: {e}")


def kb_plan_detail(p: dict):
    try:
        flags = json.loads(p.get("flags") or "{}")
    except Exception:
        flags = {}
    admin_only = bool(flags.get("admin_only"))
    test = bool(flags.get("test"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬆️ بالا", callback_data=f"admin2:plan:move:{p['id']}:up"),
                InlineKeyboardButton(text="⬇️ پایین", callback_data=f"admin2:plan:move:{p['id']}:down"),
            ],
            [InlineKeyboardButton(text="Edit Title", callback_data=f"admin2:plan:edit:{p['id']}:title")],
            [
                InlineKeyboardButton(text="Edit Days", callback_data=f"admin2:plan:edit:{p['id']}:days"),
                InlineKeyboardButton(text="Edit GB", callback_data=f"admin2:plan:edit:{p['id']}:gb"),
            ],
            [InlineKeyboardButton(text="Edit Price", callback_data=f"admin2:plan:edit:{p['id']}:price")],
            [
                InlineKeyboardButton(
                    text=f"Toggle AdminOnly: {admin_only}",
                    callback_data=f"admin2:plan:flag:{p['id']}:admin_only",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Toggle Test: {test}",
                    callback_data=f"admin2:plan:flag:{p['id']}:test",
                )
            ],
            [InlineKeyboardButton(text="Delete", callback_data=f"admin2:plan:del:{p['id']}")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin2:plans")],
        ]
    )


@router.callback_query(F.data.regexp(r"^admin2:plan:([^:]+)$"))
async def admin_plan_view(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() == PlanEdit.waiting.state:
        await state.clear()
    pid = re.match(r"^admin2:plan:([^:]+)$", cb.data).group(1)
    p = db_get_plan(pid)
    if not p:
        return await cb.answer("Plan not found")
    txt = (
        f"<b>Plan {htmlesc(p['id'])}</b>\n"
        f"Sort: {p.get('sort_order')}\n"
        f"Title: {htmlesc(p['title'])}\n"
        f"Days: {p['days']}\n"
        f"GB: {p['gb']}\n"
        f"Price: {format_toman(p['price'])}"
    )
    await cb.message.edit_text(
        txt, reply_markup=kb_plan_detail(p), parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^admin2:plan:edit:([^:]+):(title|days|gb|price)$"))
async def admin_plan_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:plan:edit:([^:]+):(title|days|gb|price)$", cb.data)
    pid = m.group(1)
    field = m.group(2)
    await state.set_state(PlanEdit.waiting)
    await state.update_data(pid=pid, field=field, back_cb=f"admin2:plan:{pid}")
    await cb.message.edit_text(
        f"Send new value for {field}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:plan:{pid}")]]
        ),
    )


@router.message(StateFilter(PlanEdit.waiting))
async def admin_plan_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("pid")
    field = data.get("field")
    back_cb = data.get("back_cb", f"admin2:plan:{pid}" if pid else "admin2:plans")
    val = (m.text or "").strip()
    if not val:
        return await m.reply("مقدار خالی مجاز نیست.")
    try:
        if field in ("days", "gb", "price"):
            val_int = int(val)
            db_update_plan_field(pid, field, val_int)
        else:
            db_update_plan_field(pid, field, val)
        await state.clear()
        await m.reply(
            "پلن به‌روزرسانی شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت به پلن", callback_data=back_cb)]]
            ),
        )
    except Exception as e:
        await m.reply(f"Update failed: {e}")


@router.callback_query(F.data.regexp(r"^admin2:plan:flag:([^:]+):(admin_only|test)$"))
async def admin_plan_toggle_flag(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:plan:flag:([^:]+):(admin_only|test)$", cb.data)
    pid = m.group(1)
    flag = m.group(2)
    p = db_get_plan(pid)
    if not p:
        return await cb.answer("Plan not found")
    try:
        flags = json.loads(p.get("flags") or "{}")
    except Exception:
        flags = {}
    flags[flag] = not bool(flags.get(flag))
    db_update_plan_field(pid, "flags", json.dumps(flags, ensure_ascii=False))
    await admin_plan_view(cb, state)


@router.callback_query(F.data.regexp(r"^admin2:plan:move:([^:]+):(up|down)$"))
async def admin_plan_move(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:plan:move:([^:]+):(up|down)$", cb.data)
    pid = m.group(1)
    direction = m.group(2)
    ok = db_swap_plan_order(pid, direction)
    if not ok:
        await cb.answer("جابجایی ممکن نیست.", show_alert=True)
    await admin_plan_view(cb, state)


@router.callback_query(F.data.regexp(r"^admin2:plan:del:([^:]+)$"))
async def admin_plan_delete(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    pid = re.match(r"^admin2:plan:del:([^:]+)$", cb.data).group(1)
    db_delete_plan(pid)
    await admin_plans(cb, state)


# --- Global discount ---


@router.callback_query(F.data == "admin:discount")
async def admin_discount(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() == DiscountEdit.waiting.state:
        await state.clear()
    pct = get_global_discount_percent()
    text = f"🎁 تخفیف سراسری فعلی: {pct}%"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="تنظیم درصد", callback_data="admin:discount:set")],
            [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")],
        ]
    )
    await cb.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "admin:discount:set")
async def admin_discount_set(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(DiscountEdit.waiting)
    await cb.message.edit_text(
        "درصد تخفیف را وارد کنید (۰ تا ۹۰):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:discount")]]),
    )


@router.message(StateFilter(DiscountEdit.waiting))
async def admin_discount_set_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("دسترسی غیرمجاز")
        return await state.clear()
    raw = (m.text or "").strip().replace("%", "")
    try:
        val = int(raw)
        if val < 0 or val > 90:
            raise ValueError("range")
    except Exception:
        return await m.reply("یک عدد بین ۰ تا ۹۰ ارسال کنید.")
    set_setting("GLOBAL_DISCOUNT_PERCENT", str(val))
    await state.clear()
    await m.reply(
        f"تخفیف سراسری روی {val}% تنظیم شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:discount")]]),
    )


# --- Templates management ---


class TemplateEdit(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:templates")
async def admin_templates_stub_redirect(cb: CallbackQuery, state: FSMContext):
    return await admin_templates(cb, state)


@router.callback_query(F.data == "admin2:templates")
async def admin_templates(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() in {TemplateEdit.waiting.state, TemplateNew.waiting.state}:
        await state.clear()
    w = get_setting("WELCOME_TEMPLATE", "(empty)")
    p = get_setting("POST_PURCHASE_TEMPLATE", "(empty)")
    txt = f"Templates:\nWELCOME_TEMPLATE:\n{w}\n\nPOST_PURCHASE_TEMPLATE:\n{p}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit Welcome", callback_data="admin2:t:edit:WELCOME_TEMPLATE")],
            [InlineKeyboardButton(text="Edit PostPurchase", callback_data="admin2:t:edit:POST_PURCHASE_TEMPLATE")],
            [InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")],
        ]
    )
    await cb.message.edit_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:t:edit:(WELCOME_TEMPLATE|POST_PURCHASE_TEMPLATE)$"))
async def admin_template_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    key = re.match(r"^admin2:t:edit:(WELCOME_TEMPLATE|POST_PURCHASE_TEMPLATE)$", cb.data).group(1)
    await state.set_state(TemplateEdit.waiting)
    await state.update_data(key=key, back_cb="admin2:templates")
    await cb.message.edit_text(
        f"Send new value for {key} (HTML allowed):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:templates")]]
        ),
    )


@router.callback_query(F.data.regexp(r"^admin2:alltpl:edit:(\d+):(.+)$"))
async def admin_alltpl_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:alltpl:edit:(\d+):(.+)$", cb.data)
    page = int(m.group(1))
    key = m.group(2)
    await state.set_state(TemplateEdit.waiting)
    await state.update_data(key=key, back_cb=f"admin2:alltpl:{page}")
    await cb.message.edit_text(
        f"Send new value for {key} (HTML allowed):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:alltpl:{page}")]]
        ),
    )


@router.message(StateFilter(TemplateEdit.waiting))
async def admin_template_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    back_cb = data.get("back_cb", "admin2:templates")
    set_setting(key, m.html_text or m.text or "")
    await state.clear()
    await m.reply(
        "Template updated.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]
        ),
    )


# --- Settings management ---


class SettingEdit(StatesGroup):
    waiting = State()

class SettingNew(StatesGroup):
    waiting = State()

class TemplateNew(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "admin:settings")
async def admin_settings_stub_redirect(cb: CallbackQuery, state: FSMContext):
    return await admin_settings(cb, state)


@router.callback_query(F.data == "admin2:settings")
async def admin_settings(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    if await state.get_state() in {SettingEdit.waiting.state, SettingNew.waiting.state}:
        await state.clear()
    lines = []
    buttons = []
    for key, label in SETTINGS_MENU:
        val = get_setting(key, "-")
        safe_val = htmlesc(str(val if val not in (None, "") else "-"))
        lines.append(f"{label}: <code>{safe_val}</code>")
        buttons.append([InlineKeyboardButton(text=f"ویرایش {label}", callback_data=f"admin2:s:edit:{key}")])
    buttons.append(
        [
            InlineKeyboardButton(text="📋 همه تنظیمات", callback_data="admin2:allsettings:0"),
            InlineKeyboardButton(text="📝 همه قالب‌ها", callback_data="admin2:alltpl:0"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")])
    txt = "تنظیمات کارت بانکی:\n" + "\n".join(lines)
    await cb.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(rf"^admin2:s:edit:({SETTINGS_KEYS_PATTERN})$"))
async def admin_settings_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    key = re.match(rf"^admin2:s:edit:({SETTINGS_KEYS_PATTERN})$", cb.data).group(1)
    await state.set_state(SettingEdit.waiting)
    await state.update_data(key=key, back_cb="admin2:settings")
    await cb.message.edit_text(
        f"Send new value for {key}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin2:settings")]]
        ),
    )


@router.message(StateFilter(SettingEdit.waiting))
async def admin_settings_edit_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    back_cb = data.get("back_cb", "admin2:settings")
    val = (m.text or "").strip()
    meta = SETTINGS_META.get(key)
    if meta:
        kind = meta.get("type")
        if kind == "int":
            try:
                num = int(val)
            except Exception:
                return await m.reply(meta.get("error", "مقدار باید عددی باشد."))
            if num < meta.get("min", 0):
                return await m.reply(meta.get("error", "مقدار باید بزرگتر از صفر باشد."))
            val = str(num)
        elif kind == "channel":
            val = val or ""
            val = val.strip()
            if val and not val.startswith("@"):
                val = "@" + val
        elif kind == "channels":
            channels = parse_channel_list(val)
            if not channels:
                return await m.reply("حداقل یک کانال وارد کنید (با @ یا لینک).")
            val = "\n".join(channels)
        elif kind == "path":
            val = (val or "/").strip() or "/"
            if not val.startswith("/"):
                val = "/" + val
            if not val.endswith("/"):
                val += "/"
        elif kind == "scheme":
            val = (val or "https").strip().lower() or "https"
    set_setting(key, val)
    await state.clear()
    await m.reply(
        "تنظیم به‌روزرسانی شد.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]
        ),
    )


# ---- Generic Settings Manager ----

def list_settings_page(where_clause:str|None, where_arg:str|None, page:int, size:int):
    off = page*size
    if where_clause:
        rows = cur.execute(f"SELECT key,value FROM settings WHERE {where_clause} ORDER BY key LIMIT ? OFFSET ?", (where_arg, size, off) if where_arg is not None else (size, off)).fetchall()
        total = cur.execute(f"SELECT COUNT(1) FROM settings WHERE {where_clause}", (where_arg,) if where_arg is not None else ()).fetchone()[0]
    else:
        rows = cur.execute("SELECT key,value FROM settings ORDER BY key LIMIT ? OFFSET ?", (size, off)).fetchall()
        total = cur.execute("SELECT COUNT(1) FROM settings").fetchone()[0]
    return [dict(r) for r in rows], total


def kb_settings_list(rows, page:int, total:int, size:int, base_cb:str):
    kb=[]
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['key']}", callback_data=f"{base_cb}:edit:{page}:{r['key']}")])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text="قبلی", callback_data=f"{base_cb}:{page-1}"))
    if (page+1)*size<total: nav.append(InlineKeyboardButton(text="بعدی", callback_data=f"{base_cb}:{page+1}"))
    if nav: kb.append(nav)
    add_cb = f"{base_cb}:add:{page}"
    if base_cb.startswith("admin2:alltpl"):
        kb.append([InlineKeyboardButton(text="Add Template", callback_data=add_cb)])
    else:
        kb.append([InlineKeyboardButton(text="Add Setting", callback_data=add_cb)])
    kb.append([InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin2:settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.regexp(r"^admin2:allsettings:(\d+)$"))
async def admin_all_settings(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    curr_state = await state.get_state()
    if curr_state in {SettingNew.waiting.state, SettingEdit.waiting.state}:
        await state.clear()
    page = int(re.match(r"^admin2:allsettings:(\d+)$", cb.data).group(1))
    size = 10
    rows, total = list_settings_page(None, None, page, size)
    if rows:
        lines = [
            f"{idx+1+page*size}. <b>{htmlesc(r['key'])}</b>: <code>{htmlesc(str(r.get('value') or ''))}</code>"
            for idx, r in enumerate(rows)
        ]
        text = "📋 تمام تنظیمات ذخیره شده:\n" + "\n".join(lines)
    else:
        text = "تنظیمی برای نمایش وجود ندارد."
    kb = kb_settings_list(rows, page, total, size, "admin2:allsettings")
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:alltpl:(\d+)$"))
async def admin_all_templates(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    curr_state = await state.get_state()
    if curr_state in {TemplateNew.waiting.state, TemplateEdit.waiting.state}:
        await state.clear()
    page = int(re.match(r"^admin2:alltpl:(\d+)$", cb.data).group(1))
    size = 10
    rows, total = list_settings_page("key LIKE ?", "%_TEMPLATE", page, size)
    if rows:
        lines = [
            f"{idx+1+page*size}. <b>{htmlesc(r['key'])}</b>: <code>{htmlesc(str(r.get('value') or ''))}</code>"
            for idx, r in enumerate(rows)
        ]
        text = "📝 قالب‌های ذخیره شده:\n" + "\n".join(lines)
    else:
        text = "قالبی برای نمایش وجود ندارد."
    kb = kb_settings_list(rows, page, total, size, "admin2:alltpl")
    await cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^admin2:allsettings:edit:(\d+):(.+)$"))
async def admin_allsettings_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    m = re.match(r"^admin2:allsettings:edit:(\d+):(.+)$", cb.data)
    page = int(m.group(1))
    key = m.group(2)
    await state.set_state(SettingEdit.waiting)
    await state.update_data(key=key, back_cb=f"admin2:allsettings:{page}")
    await cb.message.edit_text(
        f"Send new value for {key}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=f"admin2:allsettings:{page}")]]
        ),
    )


@router.callback_query(F.data.regexp(r"^admin2:allsettings:add:(\d+)$"))
async def admin_setting_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    page = int(re.match(r"^admin2:allsettings:add:(\d+)$", cb.data).group(1))
    await state.set_state(SettingNew.waiting)
    await state.update_data(back_cb=f"admin2:allsettings:{page}")
    await cb.message.edit_text(
        "کلید و مقدار را به صورت KEY=VALUE ارسال کن (کلید فقط حروف بزرگ/عدد/خط زیر).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="انصراف", callback_data=f"admin2:allsettings:{page}")]]
        ),
    )


@router.message(StateFilter(SettingNew.waiting))
async def admin_setting_add_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    back_cb = data.get("back_cb", "admin2:allsettings:0")
    text = (m.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        return await m.reply("لغو شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]))
    if "=" not in text:
        return await m.reply("فرمت باید KEY=VALUE باشد.")
    key, value = text.split("=", 1)
    key = key.strip().upper()
    value = value.strip()
    if not re.fullmatch(r"[A-Z0-9_]+", key):
        return await m.reply("کلید فقط باید شامل حروف بزرگ، اعداد و '_' باشد.")
    set_setting(key, value)
    await state.clear()
    await m.reply(
        "تنظیم جدید ذخیره شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]),
    )


@router.callback_query(F.data.regexp(r"^admin2:alltpl:add:(\d+)$"))
async def admin_template_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    page = int(re.match(r"^admin2:alltpl:add:(\d+)$", cb.data).group(1))
    await state.set_state(TemplateNew.waiting)
    await state.update_data(back_cb=f"admin2:alltpl:{page}")
    await cb.message.edit_text(
        "خط اول را با کلید (مثلاً WELCOME_TEMPLATE) و خطوط بعدی را با متن قالب (با پشتیبانی از HTML) ارسال کن.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="انصراف", callback_data=f"admin2:alltpl:{page}")]]
        ),
    )


@router.message(StateFilter(TemplateNew.waiting))
async def admin_template_add_recv(m: Message, state: FSMContext):
    data = await state.get_data()
    back_cb = data.get("back_cb", "admin2:alltpl:0")
    plain = (m.text or "").strip()
    if plain.lower() == "/cancel":
        await state.clear()
        return await m.reply("لغو شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]))
    text_plain = m.text or ""
    parts_plain = text_plain.split("\n", 1)
    key = parts_plain[0].strip().upper()
    html_payload = m.html_text or m.text or ""
    parts_html = html_payload.split("\n", 1)
    value = parts_html[1] if len(parts_html) > 1 else ""
    if not key:
        return await m.reply("کلید نمی‌تواند خالی باشد.")
    if not key.endswith("_TEMPLATE"):
        return await m.reply("کلید قالب باید با _TEMPLATE پایان یابد.")
    if not value:
        return await m.reply("متن قالب را وارد کن.")
    set_setting(key, value)
    await state.clear()
    await m.reply(
        "قالب جدید ذخیره شد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="بازگشت", callback_data=back_cb)]]),
    )


@router.callback_query(F.data == "admin:paneltest")
async def admin_paneltest(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    if not three_session:
        return await cb.message.edit_text(
            "3x-ui session not configured.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]
            ),
        )
    try:
        ibs = await three_session.list_inbounds()
        await cb.message.edit_text(
            f"Panel reachable. Inbounds: {len(ibs)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]
            ),
        )
    except Exception as e:
        await cb.message.edit_text(
            f"Panel error: {e}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="بازگشت ⬅️", callback_data="admin")]]
            ),
        )




# --- Admins management ---
class AdminEdit(StatesGroup):
    add = State()
    remove = State()

def kb_admins(ids:list[int], page:int=0, page_size:int=20):
    start=page*page_size; chunk=ids[start:start+page_size]
    kb=[]
    for uid in chunk:
        kb.append([InlineKeyboardButton(text=f"🛡️ {uid}", callback_data=f"admin:admins:remove:{uid}")])
    kb.append([InlineKeyboardButton(text="➕ افزودن ادمین", callback_data="admin:admins:add")])
    kb.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data.regexp(r"^admin:admins:(\d+)$"))
async def admin_admins_list(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    page=int(re.match(r"^admin:admins:(\d+)$", cb.data).group(1))
    ids=sorted(list(get_admin_ids()))
    await cb.message.edit_text("مدیریت ادمین‌ها:", reply_markup=kb_admins(ids,page))

@router.callback_query(F.data=="admin:admins:add")
async def admin_admins_add(cb:CallbackQuery, state:FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(AdminEdit.add)
    await cb.message.edit_text("آیدی عددی تلگرام کاربر را ارسال کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:admins:0")]]))

@router.message(StateFilter(AdminEdit.add))
async def admin_admins_add_recv(m:Message, state:FSMContext):
    try:
        uid=int(str(m.text).strip())
    except:
        return await m.reply("عدد نامعتبر است.")
    add_admin(uid)
    await state.clear()
    await m.reply(f"کاربر {uid} به فهرست ادمین‌ها افزوده شد.")

@router.callback_query(F.data.regexp(r"^admin:admins:remove:(\d+)$"))
async def admin_admins_remove(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    import re
    uid=int(re.match(r"^admin:admins:remove:(\d+)$", cb.data).group(1))
    remove_admin(uid)
    await cb.answer("حذف شد")
    await admin_admins_list(cb)




@router.callback_query(F.data=="admin:dashboard")
async def admin_dashboard(cb:CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    # Users stats (last 7 days)
    import datetime as dt
    from collections import Counter
    rows = cur.execute("SELECT created_at FROM users").fetchall()
    days = [(str(r[0])[:10]) for r in rows]
    cnt = Counter(days)
    today = dt.date.today()
    labels = []
    values = []
    for i in range(6,-1,-1):
        d = (today - dt.timedelta(days=i)).isoformat()
        labels.append(d[5:])
        values.append(cnt.get(d,0))
    total_users = cur.execute("SELECT COUNT(1) FROM users").fetchone()[0]
    # خریدها last 7 days
    rows2 = cur.execute("SELECT created_at FROM purchases").fetchall()
    days2 = [(str(r[0])[:10]) for r in rows2]
    cnt2 = Counter(days2)
    pvals = []
    for i in range(6,-1,-1):
        d=(today-dt.timedelta(days=i)).isoformat(); pvals.append(cnt2.get(d,0))
    # Top consumers
    top = cur.execute("""
        SELECT p.user_id, COALESCE(SUM(c.up + c.down),0) AS used
        FROM purchases p LEFT JOIN cache_usage c ON c.purchase_id = p.id
        GROUP BY p.user_id ORDER BY used DESC LIMIT 5
    """).fetchall()
    top_lines = []
    for t in top:
        try:
            uid=int(t[0]); used=int(t[1] or 0)
            top_lines.append(f"{uid}: {human_bytes(used)}")
        except:
            continue
    # Inbounds status
    ib_txt = "پیکربندی نشده"
    try:
        if three_session:
            ibs = await three_session.list_inbounds()
            ib_txt = f"تعداد این‌باندها: {len(ibs)}"
    except Exception as e:
        ib_txt = f"خطا در دریافت این‌باندها: {e}"
    # Compose
    chart_users = " ".join([f"{l}:{v}" for l,v in zip(labels, values)])
    chart_pur = " ".join([f"{l}:{v}" for l,v in zip(labels, pvals)])
    text = (
        "<b>📊 داشبورد</b>\n\n"
        f"👥 کاربران کل: <b>{total_users}</b>\n"
        f"کاربران ۷ روز اخیر: {chart_users}\n"
        f"خریدها ۷ روز اخیر: {chart_pur}\n\n"
        f"🔥 کاربران پرمصرف:\n" + ("\n".join(top_lines) or "-") + "\n\n"
        f"🛰️ وضعیت این‌باندها: {ib_txt}"
    )
    await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")]]))


# --- Broadcast message ---

@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(Broadcast.waiting)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin")]])
    await cb.message.edit_text("پیام همگانی را ارسال کنید (پشتیبانی از HTML):", reply_markup=kb)


@router.callback_query(F.data == "admin:broadcast:template")
async def admin_broadcast_template(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    # List available templates
    templates = []
    for key in ["WELCOME_TEMPLATE", "POST_PURCHASE_TEMPLATE", "PURCHASE_SUCCESS_TEMPLATE", "PURCHASE_FAILED_TEMPLATE", "PAYMENT_RECEIPT_TEMPLATE", "TICKET_OPENED_TEMPLATE", "TICKET_CLOSED_TEMPLATE"]:
        val = get_setting(key)
        if val:
            templates.append((key, val[:50] + "..." if len(val) > 50 else val))
    if not templates:
        await cb.answer("قالبی یافت نشد.", show_alert=True)
        return
    kb = []
    for key, preview in templates:
        kb.append([InlineKeyboardButton(text=f"{key}: {preview}", callback_data=f"admin:broadcast:send:{key}")])
    kb.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:broadcast")])
    await cb.message.edit_text("انتخاب قالب برای ارسال همگانی:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.regexp(r"^admin:broadcast:send:(.+)$"))
async def admin_broadcast_send_template(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("دسترسی غیرمجاز", show_alert=True)
    template_key = cb.data.split(":", 4)[3]
    template_text = get_setting(template_key)
    if not template_text:
        await cb.answer("قالب یافت نشد.", show_alert=True)
        return
    await _send_broadcast(cb.bot, template_text, cb.from_user.id)
    await cb.answer("پیام همگانی ارسال شد.")


@router.message(StateFilter(Broadcast.waiting))
async def admin_broadcast_send_custom(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await m.answer("دسترسی غیرمجاز")
        return await state.clear()
    data = await state.get_data()
    if data.get("broadcast_type") != "custom":
        return await state.clear()
    text = m.html_text or m.text or ""
    if not text.strip():
        return await m.reply("پیام نمی‌تواند خالی باشد.")
    await _send_broadcast(m.bot, text, m.from_user.id)
    await state.clear()
    await m.reply("پیام همگانی ارسال شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin:broadcast")]]))


async def _send_broadcast(bot, message_text: str, sender_id: int):
    users = cur.execute("SELECT user_id FROM users").fetchall()
    sent = 0
    failed = 0
    for r in users:
        uid = r["user_id"]
        try:
            await bot.send_message(uid, message_text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
    log_evt(sender_id, "broadcast", {"sent": sent, "failed": failed, "message": message_text[:200]})
