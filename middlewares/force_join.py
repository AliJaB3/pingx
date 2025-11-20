from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, CallbackQuery
from keyboards import kb_force_join
from db import get_setting
from config import REQUIRED_CHANNEL, REQUIRED_CHANNELS
from utils import parse_channel_list


class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
        if not chat or getattr(chat, "type", "private") != "private":
            return await handler(event, data)

        uid = None
        if isinstance(event, Message):
            uid = event.from_user and event.from_user.id
        elif isinstance(event, CallbackQuery):
            uid = event.from_user and event.from_user.id
        if not uid:
            return await handler(event, data)

        bot: Bot = data["bot"]
        raw = (get_setting("REQUIRED_CHANNELS", "").strip() or get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL) or REQUIRED_CHANNEL)
        channels = parse_channel_list(raw)
        missing = []
        for ch in channels:
            try:
                cm = await bot.get_chat_member(ch, uid)
                status = getattr(cm, "status", None)
                if status not in ("member", "administrator", "creator"):
                    missing.append(ch)
            except Exception:
                missing.append(ch)
        if missing:
            text = "📢 برای استفاده از ربات باید در تمام کانال‌های زیر عضو باشید."
            markup = kb_force_join(missing)
            if isinstance(event, Message):
                await event.answer(text, reply_markup=markup)
            else:
                try:
                    await event.message.edit_text(text, reply_markup=markup)
                except Exception:
                    await bot.send_message(uid, text, reply_markup=markup)
            return

        return await handler(event, data)
