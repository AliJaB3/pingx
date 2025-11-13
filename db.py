import sqlite3, json
from datetime import datetime, timezone
from config import DB_PATH, CARD_NUMBER, REQUIRED_CHANNEL, THREEXUI_INBOUND_ID, ADMIN_IDS as CONF_ADMIN_IDS
from utils import now_iso

TZ = timezone.utc
conn=sqlite3.connect(DB_PATH, isolation_level=None)
conn.row_factory=sqlite3.Row
cur=conn.cursor()
cur.execute("PRAGMA journal_mode=WAL;")
cur.execute("PRAGMA synchronous=NORMAL;")

def col_exists(table, col)->bool:
    return col in [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]

def add_col(table, col, ddl):
    if not col_exists(table, col):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl};")

def migrate():
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT, first_name TEXT, last_name TEXT,
        wallet INTEGER DEFAULT 0,
        created_at TEXT
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        note TEXT,
        photos_json TEXT,
        status TEXT,
        created_at TEXT
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS plans(
        id TEXT PRIMARY KEY,
        title TEXT, days INTEGER, gb INTEGER, price INTEGER
    );""")
    add_col("plans","flags","TEXT DEFAULT '{}'" )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan_id TEXT,
        price INTEGER
    );""")
    add_col("purchases","three_xui_client_id","TEXT")
    add_col("purchases","three_xui_inbound_id","TEXT")
    add_col("purchases","client_email","TEXT")
    add_col("purchases","sub_id","TEXT")
    add_col("purchases","sub_link","TEXT")
    add_col("purchases","allocated_gb","INTEGER DEFAULT 0")
    add_col("purchases","expiry_ms","BIGINT")
    add_col("purchases","created_at","TEXT")
    add_col("purchases","meta","TEXT")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cache_usage(
        purchase_id INTEGER PRIMARY KEY,
        up BIGINT, down BIGINT, total BIGINT,
        expiry_ms BIGINT, updated_at TEXT
    );""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        last_activity TEXT NOT NULL
    );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL,
        sender_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        content TEXT,
        caption TEXT,
        tg_msg_id INTEGER,
        created_at TEXT NOT NULL
    );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tmsg_ticket ON ticket_messages(ticket_id);")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, actor_id INTEGER, action TEXT, meta TEXT
    );""")

def set_setting(k,v): cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(k,v))
def get_setting(k, default=None):
    r=cur.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return (r and r[0]) or default

# --- Admin helpers ---
def _parse_ids_csv(csv_str:str|None)->set[int]:
    if not csv_str: return set()
    parts=[x.strip() for x in str(csv_str).replace(" ","").split(",") if x.strip()]
    res=set()
    for p in parts:
        try: res.add(int(p))
        except: continue
    return res

def get_admin_ids()->set[int]:
    extra=_parse_ids_csv(get_setting("ADMIN_IDS",""))
    base=set(int(x) for x in (CONF_ADMIN_IDS or set()))
    return base.union(extra)

def is_admin(uid:int)->bool:
    try: uid=int(uid)
    except: return False
    return uid in get_admin_ids()

def add_admin(uid:int):
    s=get_setting("ADMIN_IDS","") or ""
    ids=_parse_ids_csv(s)
    ids.add(int(uid))
    set_setting("ADMIN_IDS", ",".join(str(x) for x in sorted(ids)))

def remove_admin(uid:int):
    s=get_setting("ADMIN_IDS","") or ""
    ids=_parse_ids_csv(s)
    if int(uid) in ids: ids.remove(int(uid))
    set_setting("ADMIN_IDS", ",".join(str(x) for x in sorted(ids)))

def ensure_defaults():
    if not get_setting("ACTIVE_INBOUND_ID"): set_setting("ACTIVE_INBOUND_ID", str(THREEXUI_INBOUND_ID))
    if not get_setting("REQUIRED_CHANNEL"): set_setting("REQUIRED_CHANNEL", REQUIRED_CHANNEL)
    if not get_setting("WELCOME_TEMPLATE"): set_setting("WELCOME_TEMPLATE", "<b>به <i>PingX</i> خوش آمدید</b>\\n\\nاز دکمه‌های زیر استفاده کنید.")
    if not get_setting("POST_PURCHASE_TEMPLATE"): set_setting("POST_PURCHASE_TEMPLATE", "اشتراک شما آماده شد.")
    if not get_setting("CARD_NUMBER"): set_setting("CARD_NUMBER", CARD_NUMBER)
    if not get_setting("PURCHASE_SUCCESS_TEMPLATE"): set_setting("PURCHASE_SUCCESS_TEMPLATE", "✅ خرید با موفقیت انجام شد. لینک اشتراک برای شما ارسال شد.")
    if not get_setting("PURCHASE_FAILED_TEMPLATE"): set_setting("PURCHASE_FAILED_TEMPLATE", "❌ خرید ناموفق بود. لطفاً بعداً تلاش کنید یا با پشتیبانی در ارتباط باشید.")
    if not get_setting("PAYMENT_RECEIPT_TEMPLATE"): set_setting("PAYMENT_RECEIPT_TEMPLATE", "📩 درخواست شارژ شما ثبت شد و پس از بررسی ادمین اطلاع‌رسانی می‌شود.")
    if not get_setting("TICKET_OPENED_TEMPLATE"): set_setting("TICKET_OPENED_TEMPLATE", "🎟️ تیکت شما باز شد. لطفاً مشکل خود را توضیح دهید.")
    if not get_setting("TICKET_CLOSED_TEMPLATE"): set_setting("TICKET_CLOSED_TEMPLATE", "✅ تیکت شما بسته شد. در صورت نیاز می‌توانید تیکت جدیدی باز کنید.")

def ensure_default_plans():
    have={r[0] for r in cur.execute("SELECT id FROM plans").fetchall()}
    defs=[
        ("p1","🟢 30 روز | 100GB",30,100,150_000,{}),
        ("unlim30","♾ نامحدود | 30 روز",30,0,300_000,{}),
        ("trial1","🧪 تست ۱ روزه | نامحدود",1,0,0,{"test":True}),
        ("admtrial7","🧪 تست ۷ روزه (ادمین)",7,0,0,{"admin_only":True,"test":True}),
    ]
    for pid,title,days,gb,price,flags in defs:
        if pid in have: continue
        cur.execute("INSERT INTO plans(id,title,days,gb,price,flags) VALUES(?,?,?,?,?,?)",
                    (pid,title,days,gb,price,json.dumps(flags,ensure_ascii=False)))

def log_evt(actor_id:int, action:str, meta:dict):
    cur.execute("INSERT INTO audit_logs(ts,actor_id,action,meta) VALUES(?,?,?,?)",(now_iso(), actor_id, action, json.dumps(meta, ensure_ascii=False)))

def save_or_update_user(u):
    cur.execute("""
    INSERT INTO users(user_id,username,first_name,last_name,wallet,created_at)
    VALUES(?,?,?,?,0,?)
    ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name
    """,(u.id, u.username or "", u.first_name or "", u.last_name or "", now_iso()))

def db_get_wallet(uid:int)->int:
    r=cur.execute("SELECT wallet FROM users WHERE user_id=?", (uid,)).fetchone()
    return r[0] if r else 0

def db_add_wallet(uid:int, amount:int):
    cur.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (amount,uid))

def try_deduct_wallet(uid:int, price:int)->bool:
    cur.execute("BEGIN IMMEDIATE")
    r=cur.execute("SELECT wallet FROM users WHERE user_id=?", (uid,)).fetchone()
    bal=r[0] if r else 0
    if bal<price:
        cur.execute("ROLLBACK"); return False
    cur.execute("UPDATE users SET wallet=wallet-? WHERE user_id=?", (price,uid))
    cur.execute("COMMIT"); return True

def rollback_wallet(uid:int, price:int):
    cur.execute("BEGIN IMMEDIATE")
    cur.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (price,uid))
    cur.execute("COMMIT")

def db_new_payment(uid:int, amount:int, note:str, photos:list[str]):
    cur.execute("INSERT INTO payments(user_id,amount,note,photos_json,status,created_at) VALUES(?,?,?,?, 'pending', ?)",
                (uid, amount, note, json.dumps(photos), now_iso()))
    return cur.lastrowid

def db_list_pending_payments_page(offset:int, limit:int):
    rows=cur.execute("SELECT * FROM payments WHERE status='pending' ORDER BY id DESC LIMIT ? OFFSET ?", (limit,offset)).fetchall()
    total=cur.execute("SELECT COUNT(1) FROM payments WHERE status='pending'").fetchone()[0]
    return [dict(x) for x in rows], total

def db_get_payment(pid:int):
    r=cur.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None

def db_update_payment_status(pid:int, status:str):
    cur.execute("UPDATE payments SET status=? WHERE id=?", (status,pid))

def db_get_plan(pid:str):
    r=cur.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None

def db_list_plans():
    return [dict(r) for r in cur.execute("SELECT * FROM plans ORDER BY id").fetchall()]

def db_insert_plan(pid:str, title:str, days:int, gb:int, price:int, flags:dict|None=None):
    cur.execute(
        "INSERT INTO plans(id,title,days,gb,price,flags) VALUES(?,?,?,?,?,?)",
        (pid, title, int(days), int(gb), int(price), json.dumps(flags or {}, ensure_ascii=False)),
    )

def db_update_plan_field(pid:str, field:str, value):
    if field not in ("title","days","gb","price","flags"):
        raise ValueError("invalid field")
    cur.execute(f"UPDATE plans SET {field}=? WHERE id=?", (value, pid))

def db_delete_plan(pid:str):
    cur.execute("DELETE FROM plans WHERE id=?", (pid,))

def db_get_plans_for_user(is_admin:bool):
    rows=[dict(r) for r in cur.execute("SELECT * FROM plans ORDER BY id").fetchall()]
    res=[]
    for p in rows:
        import json
        flags=json.loads(p.get("flags") or "{}" )
        if flags.get("admin_only") and not is_admin: continue
        res.append(p)
    return res

def db_new_purchase(**kw):
    fields=["user_id","plan_id","price","three_xui_client_id","three_xui_inbound_id","client_email","sub_id","sub_link","allocated_gb","expiry_ms","created_at","meta"]
    cur.execute(f"INSERT INTO purchases ({','.join(fields)}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (kw.get("user_id"), kw.get("plan_id"), kw.get("price"),
                 kw.get("three_xui_client_id"), kw.get("three_xui_inbound_id"),
                 kw.get("client_email"), kw.get("sub_id"), kw.get("sub_link"),
                 kw.get("allocated_gb"), kw.get("expiry_ms"), now_iso(), kw.get("meta")))
    return cur.lastrowid

def user_purchases(uid:int):
    return [dict(r) for r in cur.execute("SELECT * FROM purchases WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()]

def cache_set_usage(purchase_id:int, up:int, down:int, total:int, expiry_ms:int):
    cur.execute("INSERT OR REPLACE INTO cache_usage(purchase_id,up,down,total,expiry_ms,updated_at) VALUES(?,?,?,?,?,?)",
                (purchase_id,up,down,total,expiry_ms,now_iso()))
def cache_get_usage(purchase_id:int):
    r=cur.execute("SELECT * FROM cache_usage WHERE purchase_id=?", (purchase_id,)).fetchone()
    return dict(r) if r else None

def get_or_open_ticket(uid:int)->int:
    row=cur.execute("SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    if row: return row["id"]
    cur.execute("INSERT INTO tickets(user_id,status,opened_at,closed_at,last_activity) VALUES (?,?,?,?,?)",
                (uid,"open",now_iso(), None, now_iso()))
    return cur.lastrowid

def ticket_set_activity(tid:int):
    cur.execute("UPDATE tickets SET last_activity=? WHERE id=?", (now_iso(), tid))
def ticket_close(tid:int):
    cur.execute("UPDATE tickets SET status='closed', closed_at=?, last_activity=? WHERE id=?", (now_iso(), now_iso(), tid))
def store_tmsg(tid:int, sender_type:str, sender_id:int, kind:str, content:str|None, caption:str|None, tg_msg_id:int|None):
    cur.execute("""
    INSERT INTO ticket_messages(ticket_id,sender_type,sender_id,kind,content,caption,tg_msg_id,created_at)
    VALUES(?,?,?,?,?,?,?,?)
    """,(tid,sender_type,sender_id,kind,content,caption, tg_msg_id, now_iso()))
    return cur.lastrowid

def list_tickets_page(page:int, size:int):
    off=page*size
    rows=[dict(r) for r in cur.execute("""        SELECT * FROM tickets ORDER BY (status='open') DESC, last_activity DESC LIMIT ? OFFSET ?
    """,(size,off)).fetchall()]
    total=cur.execute("SELECT COUNT(1) FROM tickets").fetchone()[0]
    return rows,total

def list_ticket_messages_page(tid:int, page:int, size:int):
    off=page*size
    rows=[dict(r) for r in cur.execute("""        SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id DESC LIMIT ? OFFSET ?
    """,(tid,size,off)).fetchall()]
    total=cur.execute("SELECT COUNT(1) FROM ticket_messages WHERE ticket_id=?",(tid,)).fetchone()[0]
    return rows,total

