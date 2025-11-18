from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, CallbackQuery
from keyboards import kb_force_join
from db import get_setting
from config import REQUIRED_CHANNEL


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
        ch = (get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL) or REQUIRED_CHANNEL).strip()
        if ch:
            cm = await bot.get_chat_member(ch, uid)
            status = getattr(cm, "status", None)
            if status not in ("member", "administrator", "creator"):
                text = "📢 برای استفاده از ربات باید عضو کانال اعلام‌شده باشید."
                if isinstance(event, Message):
                    await event.answer(text, reply_markup=kb_force_join(ch))
                else:
                    try:
                        await event.message.edit_text(text, reply_markup=kb_force_join(ch))
                    except Exception:
                        await bot.send_message(uid, text, reply_markup=kb_force_join(ch))
                return

        return await handler(event, data)
