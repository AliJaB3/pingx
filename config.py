import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","")
if not BOT_TOKEN:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS","").replace(" ","").split(",") if x}
DB_PATH = os.getenv("DB_PATH","bot.db")

REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL","@piingx").strip() or "@piingx"
REQUIRED_CHANNELS = os.getenv("REQUIRED_CHANNELS","").strip() or REQUIRED_CHANNEL

THREEXUI_BASE_URL = os.getenv("THREEXUI_BASE_URL","").rstrip("/")
THREEXUI_USERNAME  = os.getenv("THREEXUI_USERNAME","")
THREEXUI_PASSWORD  = os.getenv("THREEXUI_PASSWORD","")
THREEXUI_INBOUND_ID = int(os.getenv("THREEXUI_INBOUND_ID","39") or "39")

SUB_HOST   = os.getenv("SUB_HOST","").strip()
SUB_SCHEME = os.getenv("SUB_SCHEME","https")
SUB_PORT   = int(os.getenv("SUB_PORT","2096"))
SUB_PATH   = os.getenv("SUB_PATH","/sub/")

CARD_NUMBER = os.getenv("CARD_NUMBER","6037-XXXX-XXXX-XXXX")
MAX_RECEIPT_MB = int(os.getenv("MAX_RECEIPT_MB","5"))
MAX_RECEIPT_PHOTOS = int(os.getenv("MAX_RECEIPT_PHOTOS","3"))

PAGE_SIZE_USERS    = int(os.getenv("PAGE_SIZE_USERS","10"))
PAGE_SIZE_PAYMENTS = int(os.getenv("PAGE_SIZE_PAYMENTS","10"))
PAGE_SIZE_TICKETS  = int(os.getenv("PAGE_SIZE_TICKETS","10"))
