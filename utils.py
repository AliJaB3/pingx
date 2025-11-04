import html, math, secrets, re
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

def safe_name_from_user(user):
    if getattr(user,"username",None): return f"{user.username}@telegram"
    base = (user.first_name or "tg") + ("-" + user.last_name if user.last_name else "")
    safe=re.sub(r"[^A-Za-z0-9\-]+","-", base).strip("-") or "tg"
    return f"{safe}-{user.id}@telegram"

def qr_bytes(data:str)->BytesIO:
    img=qrcode.make(data); bio=BytesIO(); img.save(bio,format="PNG"); bio.seek(0); return bio
