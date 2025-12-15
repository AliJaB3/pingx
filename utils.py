import html, math, secrets, re
from typing import Any
from datetime import datetime, timezone
from io import BytesIO
import qrcode

TZ = timezone.utc

def htmlesc(x:str)->str: return html.escape(x or "")

def human_bytes(b:int)->str:
    if b<=0: return "0 B"
    units=["B","KB","MB","GB","TB"]; i=int(math.floor(math.log(b,1024))); return f"{b/1024**i:.1f} {units[i]}"

def progress_bar(p:float,w:int=20)->str:
    p=max(0,min(1,p)); f=int(round(p*w)); return "█"*f + "░"*(w-f)

def now_iso(): return datetime.now(TZ).isoformat()

def format_toman(amount:int|float)->str:
    try:
        amt = int(round(float(amount)))
    except Exception:
        try:
            amt = int(amount)
        except Exception:
            return f"{amount} تومان"
    return f"{amt:,} تومان"

def format_identity(user_id:int, username:str|None, full_name:str|None)->str:
    if username:
        return f"@{htmlesc(username)}"
    display = htmlesc(full_name or str(user_id))
    return f'<a href="tg://user?id={user_id}">{display}</a>'

def safe_name_from_user(user):
    if getattr(user,"username",None): return f"{user.username}@telegram"
    base = (user.first_name or "tg") + ("-" + user.last_name if user.last_name else "")
    safe=re.sub(r"[^A-Za-z0-9\-]+","-", base).strip("-") or "tg"
    return f"{safe}-{user.id}@telegram"

def qr_bytes(data:str)->BytesIO:
    img=qrcode.make(data); bio=BytesIO(); img.save(bio,format="PNG"); bio.seek(0); return bio


def normalize_channel_handle(ch: str) -> str:
    ch = (ch or "").strip()
    if not ch:
        return ""
    low = ch.lower()
    if "t.me/" in low:
        ch = ch.split("t.me/", 1)[1]
    ch = ch.split("?")[0].strip().strip("/")
    if not ch:
        return ""
    if ch.startswith("-") or ch.lstrip("-").isdigit():
        if not ch.startswith("-"):
            ch = "-" + ch
        if not ch.startswith("-100"):
            ch = "-100" + ch.lstrip("-")
        return ch
    if ch.startswith("@"):
        return ch
    return "@" + ch


def parse_channel_list(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,\n]+", value)
    seen: list[str] = []
    for part in parts:
        ch = normalize_channel_handle(part)
        if ch and ch not in seen:
            seen.append(ch)
    return seen


async def fetch_channel_details(bot: Any, channels: list[str]):
    details = []
    for ch in channels:
        if not ch:
            continue
        chat_id: Any = ch
        if not ch.startswith("@"):
            try:
                chat_id = int(ch)
            except Exception:
                chat_id = ch
        label = ch.lstrip("@") if ch.startswith("@") else ch
        url = None
        try:
            chat = await bot.get_chat(chat_id)
            label = chat.title or chat.username or label
            username = getattr(chat, "username", None)
            invite = getattr(chat, "invite_link", None)
            if username:
                url = f"https://t.me/{username}"
            elif invite:
                url = invite
        except Exception:
            if ch.startswith("@"):
                url = f"https://t.me/{ch.lstrip('@')}"
        details.append({"id": ch, "label": label, "url": url})
    return details
