import asyncio, math
from datetime import datetime
from aiogram import Bot
from db import cur
from utils import TZ


async def scheduler(bot: Bot):
    await asyncio.sleep(5)
    while True:
        try:
            rows = [dict(r) for r in cur.execute("SELECT * FROM purchases").fetchall()]
            now_ms = int(datetime.now(TZ).timestamp() * 1000)
            for r in rows:
                uid = r["user_id"]
                pid = r["id"]
                cached = cur.execute("SELECT * FROM cache_usage WHERE purchase_id=?", (pid,)).fetchone()
                if cached:
                    used = int(cached["up"] or 0) + int(cached["down"] or 0)
                    total = int(cached["total"] or 0)
                    if total > 0:
                        pct = used / total
                        if 0.80 <= pct < 0.83:
                            try:
                                await bot.send_message(
                                    uid,
                                    f"مصرف شما به {int(pct*100)}٪ از حجم بسته رسیده است.",
                                )
                            except Exception:
                                pass
                expiry_ms = int(r["expiry_ms"] or 0)
                if expiry_ms > 0:
                    days_left = math.ceil((expiry_ms - now_ms) / 1000 / 3600 / 24)
                    if days_left in (3, 1):
                        try:
                            await bot.send_message(
                                uid,
                                f"اشتراک شما تا {days_left} روز دیگر منقضی می‌شود.",
                            )
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(3600)
