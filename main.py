import asyncio
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

async def main():
    migrate(); ensure_defaults(); ensure_default_plans()
    bot=Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher()
    dp.update.middleware(ForceJoinMiddleware())

    dp.include_router(user_handlers.router)
    dp.include_router(payment_handlers.router)
    dp.include_router(ticket_handlers.router)
    dp.include_router(admin_handlers.router)

    asyncio.create_task(scheduler(bot))

    print("PingX bot started (modular)." )
    await dp.start_polling(bot)

if __name__=="__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Stopped.")
