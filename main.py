import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import BOT_TOKEN
from db import migrate, ensure_defaults, ensure_default_plans
from handlers import user as user_handlers
from handlers import payments as payment_handlers
from handlers import tickets as ticket_handlers
from handlers import admin as admin_handlers
from scheduler import scheduler
from middlewares.force_join import ForceJoinMiddleware
from middlewares.logging_middleware import LoggingMiddleware


def setup_logging():
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "bot.log"
    events_file = log_dir / "events.log"
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)

    events_logger = logging.getLogger("events")
    events_logger.setLevel(logging.INFO)
    if not events_logger.handlers:
        events_logger.addHandler(RotatingFileHandler(events_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
        events_logger.propagate = False

    return logging.getLogger("pingx"), events_logger


async def main():
    logger, events_logger = setup_logging()
    migrate(); ensure_defaults(); ensure_default_plans()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.update.middleware(LoggingMiddleware(events_logger))
    dp.update.middleware(ForceJoinMiddleware())

    @dp.errors()
    async def on_error(event, exception):
        logger.exception("Unhandled error in update", exc_info=exception)
        return True

    dp.include_router(user_handlers.router)
    dp.include_router(payment_handlers.router)
    dp.include_router(ticket_handlers.router)
    dp.include_router(admin_handlers.router)

    asyncio.create_task(scheduler(bot))

    print("PingX bot started (modular).")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Stopped.")
