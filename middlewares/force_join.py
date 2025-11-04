from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram import Bot
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
        try:
            # Only private chats
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

            # Whitelist certain callbacks to avoid loops
            if isinstance(event, CallbackQuery) and event.data in ("recheck_join",):
                pass  # allow check to run below

            bot: Bot = data["bot"]
            ch = (get_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL) or REQUIRED_CHANNEL).strip()
            if ch:
                try:
                    cm = await bot.get_chat_member(ch, uid)
                    status = getattr(cm, "status", None)
                    if status not in ("member", "administrator", "creator"):
                        raise Exception("not member")
                except Exception:
                    # Not a member: prompt and stop propagation
                    text = "برای استفاده از ربات، ابتدا در کانال عضو شوید."
                    if isinstance(event, Message):
                        await event.answer(text, reply_markup=kb_force_join(ch))
                    else:
                        try:
                            await event.message.edit_text(text, reply_markup=kb_force_join(ch))
                        except Exception:
                            await bot.send_message(uid, text, reply_markup=kb_force_join(ch))
                    return
        except Exception:
            # Fail-open to avoid blocking bot in edge cases
            return await handler(event, data)

        return await handler(event, data)

