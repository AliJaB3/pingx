import asyncio
import math
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
                        last_usage_warn = (cached["last_usage_warn"] or "").strip() if "last_usage_warn" in cached.keys() else ""
                        if 0.80 <= pct < 0.83 and last_usage_warn != "80":
                            try:
                                await bot.send_message(
                                    uid,
                                    f"�???�??�???? �???�??�??�???�??? �???�???�??? �???�??? {int(pct*100)}�??? �???�??�?? �???�???�??? �???�???�???�??? �???�???�???�???�??? �???�???�???.",
                                )
                                cur.execute("UPDATE cache_usage SET last_usage_warn=? WHERE purchase_id=?", ("80", pid))
                            except Exception:
                                pass

                expiry_ms = int(r["expiry_ms"] or 0)
                if expiry_ms > 0:
                    days_left = math.ceil((expiry_ms - now_ms) / 1000 / 3600 / 24)
                    try:
                        last_exp_notice = int(r.get("last_expiry_notice"))
                    except Exception:
                        last_exp_notice = None
                    last_at = r.get("last_expiry_notice_at")
                    recent = False
                    if last_at:
                        try:
                            last_dt = datetime.fromisoformat(last_at)
                            recent = (datetime.now(TZ) - last_dt).total_seconds() < 86_400
                        except Exception:
                            recent = False
                    if days_left > 0 and days_left in (3, 1) and last_exp_notice != days_left and not recent:
                        try:
                            await bot.send_message(
                                uid,
                                f"�???�?? �???�???�???�???�???�??? �???�???�??? �???�??? {days_left} �???�???�??�?? �???�???�???�??? �???�???�???�???�??? �???�???�????�???�???�???.",
                            )
                            cur.execute(
                                "UPDATE purchases SET last_expiry_notice=?, last_expiry_notice_at=? WHERE id=?",
                                (days_left, datetime.now(TZ).isoformat(), pid),
                            )
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(3600)
