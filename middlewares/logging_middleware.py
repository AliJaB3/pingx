import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from typing import Any, Callable, Awaitable


class LoggingMiddleware(BaseMiddleware):
    def __init__(self, logger: logging.Logger):
        super().__init__()
        self.logger = logger

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        state_name = None
        try:
            state: FSMContext | None = data.get("state")  # type: ignore
            if state:
                try:
                    state_name = await state.get_state()
                except Exception:
                    state_name = None

            uid = None
            chat_id = None
            desc = ""
            if isinstance(event, Message):
                uid = getattr(event.from_user, "id", None)
                chat_id = getattr(event.chat, "id", None)
                txt = (event.text or event.caption or "")[:200].replace("\n", " ")
                desc = f"Message: {txt}"
            elif isinstance(event, CallbackQuery):
                uid = getattr(event.from_user, "id", None)
                chat_id = getattr(getattr(event.message, "chat", None), "id", None)
                data_val = (event.data or "")[:150]
                desc = f"Callback: {data_val}"
            self.logger.info("event=%s uid=%s chat=%s state=%s %s", type(event).__name__, uid, chat_id, state_name, desc)
        except Exception:
            # Avoid blocking flow on logging errors
            pass
        try:
            return await handler(event, data)
        except Exception:
            try:
                self.logger.exception("Handler error for event %s state=%s", type(event).__name__, state_name)
            except Exception:
                pass
            raise
