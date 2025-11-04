\
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from config import CARD_NUMBER, MAX_RECEIPT_PHOTOS, ADMIN_IDS, PAGE_SIZE_PAYMENTS
from db import db_get_wallet, db_new_payment, db_get_payment, db_add_wallet, db_update_payment_status, db_list_pending_payments_page, cur
from keyboards import kb_admin_root
from utils import htmlesc
import json

router = Router()

class Topup(StatesGroup): amount=State(); note=State()

@router.callback_query(F.data==\"wallet\")
async def wallet(cb:CallbackQuery):
    bal=db_get_wallet(cb.from_user.id)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=\"➕ شارژ کیف پول\", callback_data=\"topup\")],
        [InlineKeyboardButton(text=\"⬅️ برگشت\", callback_data=\"home\")]
    ])
    await cb.message.edit_text(f\"💼 موجودی فعلی: <b>{bal:,} تومان</b>\", reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data==\"topup\")
async def topup_ask_amount(cb:CallbackQuery, state:FSMContext):
    await state.set_state(Topup.amount)
    msg = (
        \"<b>شارژ کیف پول</b>\\n\\n\"
        \"» مبلغ را به کارت زیر واریز کنید و <b>عدد مبلغ</b> را اینجا ارسال کنید.\\n\\n\"
        f\"<b>کارت:</b> <code>{CARD_NUMBER}</code>\\n\"
        \"مثال: 150000\\n\\n\"
        f\"عکس رسید: حداکثر {MAX_RECEIPT_PHOTOS} عدد. سپس کلمهٔ «تمام» را بفرستید.\"
    )
    await cb.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ برگشت\", callback_data=\"wallet\")]]), parse_mode=ParseMode.HTML)

@router.message(Topup.amount)
async def topup_got_amount(m:Message, state:FSMContext):
    try: amount=int(str(m.text).replace(\",\",\"\" ).strip())
    except: await m.reply(\"مبلغ نامعتبره. فقط عدد بفرست.\"); return
    await state.update_data(amount=amount, photos=[]); await state.set_state(Topup.note)
    await m.reply(\"توضیح/رسید را بفرستید و در پایان «تمام» را ارسال کنید.\")

@router.message(F.photo)
async def collect_photo(m:Message, state:FSMContext):
    s=await state.get_state()
    if not s or \"Topup\" not in s: return
    data=await state.get_data(); photos=data.get(\"photos\",[])
    if len(photos) >= MAX_RECEIPT_PHOTOS:
        await m.reply(\"حداکثر تعداد عکس رسید به حد نصاب رسیده است.\"); return
    photos.append(m.photo[-1].file_id); await state.update_data(photos=photos)
    await m.reply(f\"رسید ذخیره شد ({len(photos)}/{MAX_RECEIPT_PHOTOS}).\")

@router.message(Topup.note)
async def topup_collect(m:Message, state:FSMContext):
    data=await state.get_data(); amount=data.get(\"amount\"); photos=data.get(\"photos\",[])
    if m.text and m.text.strip()==\"تمام\":
        pid=db_new_payment(m.from_user.id, amount, data.get(\"note\",\"\"), photos); await state.clear()
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"🔎 جزئیات پرداخت\", callback_data=f\"payview:{pid}\")]])
        for aid in ADMIN_IDS:
            try:
                if photos:
                    await m.bot.send_message(aid, f\"💳 درخواست شارژ جدید #{pid}\\nاز <a href=\\\"tg://user?id={m.from_user.id}\\\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\\nمبلغ: {amount:,}\", parse_mode=ParseMode.HTML)
                    for ph in photos: await m.bot.send_photo(aid, ph, caption=f\"رسید پرداخت #{pid}\")
                    await m.bot.send_message(aid, \"اقدام:\", reply_markup=kb)
                else:
                    await m.bot.send_message(aid, f\"💳 درخواست شارژ جدید #{pid}\\nاز <a href=\\\"tg://user?id={m.from_user.id}\\\">{htmlesc(m.from_user.full_name or m.from_user.username or str(m.from_user.id))}</a>\\nمبلغ: {amount:,}\", reply_markup=kb, parse_mode=ParseMode.HTML)
            except: pass
        await m.reply(\"درخواست شارژ ثبت شد. پس از تایید ادمین به موجودی شما اضافه می‌شود.\")
    else:
        await state.update_data(note=m.text or \"\")
        await m.reply(\"اگر پایان یافت، «تمام» را بفرستید.\")

@router.callback_query(F.data.regexp(r\"^admin:pending:(\\d+)$\"))
async def admin_pending(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    import re; page=int(re.match(r\"^admin:pending:(\\d+)$\", cb.data).group(1))
    limit=PAGE_SIZE_PAYMENTS; offset=page*limit
    pend,total=db_list_pending_payments_page(offset, limit)
    if not pend:
        await cb.message.edit_text(\"درخواستی در انتظار نیست.\", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")]])); return
    rows=[[InlineKeyboardButton(text=f\"#{p['id']} — {p['amount']:,} — user {p['user_id']}\", callback_data=f\"payview:{p['id']}\")] for p in pend]
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text=\"⬅️ قبلی\", callback_data=f\"admin:pending:{page-1}\"))
    if (page+1)*limit<total: nav.append(InlineKeyboardButton(text=\"بعدی ➡️\", callback_data=f\"admin:pending:{page+1}\"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton(text=\"⬅️ مدیریت\", callback_data=\"admin\")])
    await cb.message.edit_text(\"پرداخت‌های در انتظار:\", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith(\"payview:\"))
async def admin_pay_view(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    pid=int(cb.data.split(\":\")[1]); p=db_get_payment(pid)
    if not p: return await cb.answer(\"یافت نشد\")
    u=cur.execute(\"SELECT * FROM users WHERE user_id=?\", (p[\"user_id\"],)).fetchone()
    nm=(\" \".join(filter(None,[u and u[\"first_name\"] or \"\", u and u[\"last_name\"] or \"\"])) or (u and u[\"username\"] or str(p[\"user_id\"]))).strip()
    user_html=f'<a href=\"tg://user?id={p[\"user_id\"]}\">{htmlesc(nm)}</a>'
    caption=(f\"💳 <b>جزئیات پرداخت #{p['id']}</b>\\nکاربر: {user_html}\\nمبلغ: <b>{p['amount']:,} تومان</b>\\n\"
             f\"توضیح: {htmlesc(p['note'] or '-')}\\nوضعیت: <b>{p['status']}</b>\\nتاریخ: {p['created_at'][:19].replace('T',' ')}\")
    photos=json.loads(p.get(\"photos_json\") or \"[]\")
    def kb(pid:int):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=\"تایید ✅\", callback_data=f\"payok:{pid}\"),
             InlineKeyboardButton(text=\"رد ❌\", callback_data=f\"payno:{pid}\")],
            [InlineKeyboardButton(text=\"⬅️ برگشت\", callback_data=\"admin:pending:0\")]
        ])
    if photos:
        try: await cb.message.delete()
        except: pass
        await cb.bot.send_message(cb.from_user.id, caption, parse_mode=ParseMode.HTML)
        for ph in photos: await cb.bot.send_photo(cb.from_user.id, ph, caption=f\"رسید #{p['id']}\")
        await cb.bot.send_message(cb.from_user.id, \"اقدام:\", reply_markup=kb(pid))
    else:
        await cb.message.edit_text(caption, reply_markup=kb(pid), parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith(\"payok:\"))
async def admin_pay_ok(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    pid=int(cb.data.split(\":\")[1]); row=db_get_payment(pid)
    if not row or row[\"status\"]!=\"pending\": return await cb.answer(\"نامعتبر/انجام شده\")
    db_update_payment_status(pid,\"approved\"); db_add_wallet(row[\"user_id\"], row[\"amount\"])
    try: await cb.bot.send_message(row[\"user_id\"], f\"✅ شارژ {row['amount']:,} تومان تایید شد. موجودی شما به‌روز شد.\")
    except: pass
    await admin_pending(cb)

@router.callback_query(F.data.startswith(\"payno:\"))
async def admin_pay_no(cb:CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return await cb.answer(\"ندارید\", show_alert=True)
    pid=int(cb.data.split(\":\")[1]); row=db_get_payment(pid)
    if not row or row[\"status\"]!=\"pending\": return await cb.answer(\"نامعتبر/انجام شده\")
    db_update_payment_status(pid,\"rejected\")
    try: await cb.bot.send_message(row[\"user_id\"], f\"❌ شارژ شما به مبلغ {row['amount']:,} تومان رد شد.\")
    except: pass
    await admin_pending(cb)
