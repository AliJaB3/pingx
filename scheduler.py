import asyncio
import logging
import math
from datetime import datetime
from aiogram import Bot
from db import cur, cache_set_usage, list_active_purchases
from utils import TZ
from xui import three_session

logger = logging.getLogger("pingx.scheduler")


async def _sync_usage_cache():
    if not three_session:
        return
    now_ms = int(datetime.now(TZ).timestamp() * 1000)
    active = list_active_purchases(now_ms=now_ms)
    for r in active:
        try:
            inbound_id = int(r["three_xui_inbound_id"])
            client_id = r["three_xui_client_id"]
            stat = await three_session.get_client_stats(inbound_id, client_id, r.get("client_email"))
            if not stat:
                continue
            total = int(stat.get("total") or 0)
            if total <= 0 and int(r.get("allocated_gb") or 0) > 0:
                total = int(r["allocated_gb"]) * 1024**3
            expiry = int(stat.get("expiryTime") or r.get("expiry_ms") or 0)
            cache_set_usage(r["id"], int(stat.get("up") or 0), int(stat.get("down") or 0), total, expiry)
        except Exception:
            logger.exception("usage sync failed pid=%s", r.get("id"))


async def scheduler(bot: Bot):
    await asyncio.sleep(5)
    while True:
        try:
            await _sync_usage_cache()
        except Exception:
            logger.exception("scheduler usage sync error")
        try:
            rows = [dict(r) for r in cur.execute("SELECT * FROM purchases WHERE active=1").fetchall()]
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
                                await bot.send_message(uid, f"⚠️ مصرف شما به {int(pct*100)}٪ نزدیک شده است.")
                                cur.execute("UPDATE cache_usage SET last_usage_warn=? WHERE purchase_id=?", ("80", pid))
                            except Exception:
                                logger.warning("send usage warn failed pid=%s uid=%s", pid, uid, exc_info=True)

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
                            await bot.send_message(uid, f"⏳ اشتراک شما {days_left} روز دیگر منقضی می‌شود.")
                            cur.execute(
                                "UPDATE purchases SET last_expiry_notice=?, last_expiry_notice_at=? WHERE id=?",
                                (days_left, datetime.now(TZ).isoformat(), pid),
                            )
                        except Exception:
                            logger.warning("send expiry warn failed pid=%s uid=%s", pid, uid, exc_info=True)
        except Exception:
            logger.exception("scheduler loop error")
        await asyncio.sleep(1800)
