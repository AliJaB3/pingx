import sqlite3, json
from datetime import datetime, timezone
from config import (
    DB_PATH,
    CARD_NUMBER,
    REQUIRED_CHANNEL,
    THREEXUI_INBOUND_ID,
    ADMIN_IDS as CONF_ADMIN_IDS,
    SUB_HOST,
    SUB_SCHEME,
    SUB_PATH,
    SUB_PORT,
    MAX_RECEIPT_PHOTOS,
    MAX_RECEIPT_MB,
)
from utils import now_iso

TZ = timezone.utc
conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=5, check_same_thread=False)
conn.execute("PRAGMA busy_timeout=5000;")
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("PRAGMA journal_mode=WAL;")
cur.execute("PRAGMA synchronous=NORMAL;")


def col_exists(table, col) -> bool:
    return col in [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]


def add_col(table, col, ddl):
    if not col_exists(table, col):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl};")


def migrate():
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT, first_name TEXT, last_name TEXT,
        wallet INTEGER DEFAULT 0,
        created_at TEXT
    );"""
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        note TEXT,
        photos_json TEXT,
        status TEXT,
        created_at TEXT
    );"""
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS plans(
        id TEXT PRIMARY KEY,
        title TEXT, days INTEGER, gb INTEGER, price INTEGER
    );"""
    )
    add_col("plans", "flags", "TEXT DEFAULT '{}' ")
    add_col("plans", "sort_order", "INTEGER NOT NULL DEFAULT 0")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan_id TEXT,
        price INTEGER
    );"""
    )
    add_col("purchases", "three_xui_client_id", "TEXT")
    add_col("purchases", "three_xui_inbound_id", "TEXT")
    add_col("purchases", "client_email", "TEXT")
    add_col("purchases", "sub_id", "TEXT")
    add_col("purchases", "sub_link", "TEXT")
    add_col("purchases", "allocated_gb", "INTEGER DEFAULT 0")
    add_col("purchases", "expiry_ms", "BIGINT")
    add_col("purchases", "created_at", "TEXT")
    add_col("purchases", "meta", "TEXT")
    add_col("purchases", "last_expiry_notice", "INTEGER")
    add_col("purchases", "last_expiry_notice_at", "TEXT")
    add_col("purchases", "active", "INTEGER NOT NULL DEFAULT 1")
    add_col("purchases", "superseded_by", "INTEGER")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cache_usage(
        purchase_id INTEGER PRIMARY KEY,
        up BIGINT, down BIGINT, total BIGINT,
        expiry_ms BIGINT, updated_at TEXT
    );"""
    )
    add_col("cache_usage", "last_usage_warn", "TEXT")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        last_activity TEXT NOT NULL
    );"""
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS ticket_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL,
        sender_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        content TEXT,
        caption TEXT,
        tg_msg_id INTEGER,
        created_at TEXT NOT NULL,
        src_chat_id INTEGER,
        src_message_id INTEGER
    );"""
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tmsg_ticket ON ticket_messages(ticket_id);")
    add_col("ticket_messages", "src_chat_id", "INTEGER")
    add_col("ticket_messages", "src_message_id", "INTEGER")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tmsg_src ON ticket_messages(ticket_id,src_chat_id,src_message_id);")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, actor_id INTEGER, action TEXT, meta TEXT
    );"""
    )

    # Referral codes for tracking user acquisition
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS referral_links(
        code TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        created_by INTEGER,
        created_at TEXT,
        clicks INTEGER DEFAULT 0,
        signups INTEGER DEFAULT 0
    );
    """
    )
    add_col("referral_links", "description", "TEXT")
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS referral_joins(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        user_id INTEGER,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        joined_at TEXT
    );
    """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_referral_joins_code ON referral_joins(code);")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event TEXT NOT NULL,
        meta_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );"""
    )


def set_setting(k, v):
    cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))


def get_setting(k, default=None):
    r = cur.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return (r and r[0]) or default


def _parse_ids_csv(csv_str: str | None) -> set[int]:
    if not csv_str:
        return set()
    parts = [x.strip() for x in str(csv_str).replace(" ", "").split(",") if x.strip()]
    res = set()
    for p in parts:
        try:
            res.add(int(p))
        except Exception:
            continue
    return res


def get_admin_ids() -> set[int]:
    extra = _parse_ids_csv(get_setting("ADMIN_IDS", ""))
    base = set(int(x) for x in (CONF_ADMIN_IDS or set()))
    return base.union(extra)


def is_admin(uid: int) -> bool:
    try:
        uid = int(uid)
    except Exception:
        return False
    return uid in get_admin_ids()


def add_admin(uid: int):
    s = get_setting("ADMIN_IDS", "") or ""
    ids = _parse_ids_csv(s)
    ids.add(int(uid))
    set_setting("ADMIN_IDS", ",".join(str(x) for x in sorted(ids)))


def remove_admin(uid: int):
    s = get_setting("ADMIN_IDS", "") or ""
    ids = _parse_ids_csv(s)
    if int(uid) in ids:
        ids.remove(int(uid))
    set_setting("ADMIN_IDS", ",".join(str(x) for x in sorted(ids)))


def get_support_ids() -> set[int]:
    return _parse_ids_csv(get_setting("SUPPORT_IDS", ""))


def is_support(uid: int) -> bool:
    try:
        uid = int(uid)
    except Exception:
        return False
    return uid in get_support_ids()


def is_staff(uid: int) -> bool:
    return is_admin(uid) or is_support(uid)


def add_support(uid: int):
    ids = _parse_ids_csv(get_setting("SUPPORT_IDS", ""))
    ids.add(int(uid))
    set_setting("SUPPORT_IDS", ",".join(str(x) for x in sorted(ids)))


def remove_support(uid: int):
    ids = _parse_ids_csv(get_setting("SUPPORT_IDS", ""))
    if int(uid) in ids:
        ids.remove(int(uid))
    set_setting("SUPPORT_IDS", ",".join(str(x) for x in sorted(ids)))


def ensure_defaults():
    # Only backfill defaults when a value is missing; don't override panel changes.
    def set_if_missing(key: str, value):
        if get_setting(key) is None:
            set_setting(key, value)

    set_if_missing("ACTIVE_INBOUND_ID", str(THREEXUI_INBOUND_ID))
    set_if_missing("REQUIRED_CHANNEL", REQUIRED_CHANNEL)
    if not (get_setting("REQUIRED_CHANNELS") or "").strip():
        set_setting("REQUIRED_CHANNELS", REQUIRED_CHANNEL)
    if not get_setting("WELCOME_TEMPLATE"):
        set_setting("WELCOME_TEMPLATE", "👋 به پینگ‌ایکس خوش آمدی!")
    if not get_setting("POST_PURCHASE_TEMPLATE"):
        set_setting("POST_PURCHASE_TEMPLATE", "✅ خرید انجام شد و لینک اشتراک ارسال شد.")
    if not get_setting("CARD_NUMBER"):
        set_setting("CARD_NUMBER", CARD_NUMBER)
    set_if_missing("SUB_HOST", SUB_HOST or "")
    set_if_missing("SUB_SCHEME", SUB_SCHEME or "https")
    set_if_missing("SUB_PATH", SUB_PATH or "/sub/")
    set_if_missing("SUB_PORT", str(SUB_PORT))
    set_if_missing("MAX_RECEIPT_PHOTOS", str(MAX_RECEIPT_PHOTOS))
    set_if_missing("MAX_RECEIPT_MB", str(MAX_RECEIPT_MB))
    if not get_setting("PURCHASE_SUCCESS_TEMPLATE"):
        set_setting("PURCHASE_SUCCESS_TEMPLATE", "🥳 اشتراک شما آماده شد. لینک برایتان ارسال شد.")
    if not get_setting("PURCHASE_FAILED_TEMPLATE"):
        set_setting("PURCHASE_FAILED_TEMPLATE", "⚠️ ایجاد اشتراک با خطا مواجه شد. لطفاً با پشتیبانی در تماس باشید.")
    if not get_setting("PAYMENT_RECEIPT_TEMPLATE"):
        set_setting("PAYMENT_RECEIPT_TEMPLATE", "🧾 درخواست پرداخت شما ثبت شد و پس از بررسی اطلاع داده می‌شود.")
    if not get_setting("TICKET_OPENED_TEMPLATE"):
        set_setting("TICKET_OPENED_TEMPLATE", "🆘 تیکت شما باز شد. پیام خود را بفرستید.")
    if not get_setting("TICKET_CLOSED_TEMPLATE"):
        set_setting("TICKET_CLOSED_TEMPLATE", "✅ تیکت بسته شد. برای تیکت جدید دوباره پیام بدهید.")


    set_if_missing("GLOBAL_DISCOUNT_PERCENT", "0")
    set_if_missing("SUPPORT_IDS", "")


def ensure_default_plans():
    migration_flag = "PLANS_V2_MIGRATED"
    legacy_ids = ("p1", "unlim30")
    if not get_setting(migration_flag):
        cur.executemany("DELETE FROM plans WHERE id=?", [(pid,) for pid in legacy_ids])
        set_setting(migration_flag, "1")
    defs = [
        ("vol_lite", "پینگ لایت ⚡️ | ۲۵ گیگ | ۲ دستگاه | شروع اقتصادی", 30, 25, 49_000, {"device_limit": 2}),
        ("vol_plus", "پینگ پلاس 🚀 | ۵۰ گیگ | ۲ دستگاه | مصرف روزمره و استریم سبک", 30, 50, 85_000, {"device_limit": 2}),
        ("vol_pro", "پینگ پرو 💎 | ۱۰۰ گیگ | ۳ دستگاه | مناسب گیم و حرفه‌ای", 30, 100, 150_000, {"device_limit": 3}),
        ("vol_ultra", "پینگ الترا 🏆 | ۲۰۰ گیگ | ۳ دستگاه | خانواده‌ها و پرمصرف‌ها", 30, 200, 249_000, {"device_limit": 3}),
        ("time_gold", "ماهانه طلایی | ۳۰ روز | ۲ دستگاه | نامحدود واقعی", 30, 0, 99_000, {"device_limit": 2}),
        ("time_platinum", "سه‌ماهه پلاتینیوم | ۹۰ روز | ۳ دستگاه | پشتیبانی VIP + سرور گیم", 90, 0, 269_000, {"device_limit": 3}),
        ("time_premium", "شش‌ماهه پریمیوم | ۱۸۰ روز | ۳ دستگاه | سرور اختصاصی و پایدار", 180, 0, 499_000, {"device_limit": 3}),
        ("time_diamond", "سالانه دیاموند | ۳۶۵ روز | ۳ دستگاه | تمدید خودکار و پشتیبانی ویژه", 365, 0, 899_000, {"device_limit": 3}),
        ("trial1", "تست ۱ روزه | رایگان", 1, 0, 0, {"test": True}),
        ("admtrial7", "تست ۷ روزه (ادمین)", 7, 0, 0, {"admin_only": True, "test": True}),
    ]
    for pid, title, days, gb, price, flags in defs:
        row = cur.execute("SELECT id FROM plans WHERE id=?", (pid,)).fetchone()
        if row:
            cur.execute(
                "UPDATE plans SET title=?, days=?, gb=?, price=?, flags=? WHERE id=?",
                (title, days, gb, price, json.dumps(flags, ensure_ascii=False), pid),
            )
        else:
            cur.execute(
                "INSERT INTO plans(id,title,days,gb,price,flags) VALUES(?,?,?,?,?,?)",
                (pid, title, days, gb, price, json.dumps(flags, ensure_ascii=False)),
            )


def log_evt(actor_id: int, action: str, meta: dict):
    cur.execute("INSERT INTO audit_logs(ts,actor_id,action,meta) VALUES(?,?,?,?)", (now_iso(), actor_id, action, json.dumps(meta, ensure_ascii=False)))


def log_event(user_id: int, event: str, meta: dict | None = None):
    cur.execute(
        "INSERT INTO events(user_id,event,meta_json,created_at) VALUES(?,?,?,?)",
        (user_id, event, json.dumps(meta or {}, ensure_ascii=False), now_iso()),
    )


def count_users() -> int:
    return cur.execute("SELECT COUNT(1) FROM users").fetchone()[0]


# Referral helpers
def create_referral(code: str, title: str, created_by: int, description: str = ""):
    cur.execute(
        "INSERT INTO referral_links(code,title,description,created_by,created_at,clicks,signups) VALUES(?,?,?,?,?,0,0)",
        (code, title, description, int(created_by), now_iso()),
    )


def list_referrals():
    return [dict(r) for r in cur.execute("SELECT * FROM referral_links ORDER BY created_at DESC").fetchall()]


def get_referral(code: str):
    r = cur.execute("SELECT * FROM referral_links WHERE code=?", (code,)).fetchone()
    return dict(r) if r else None


def update_referral_title(code: str, title: str):
    cur.execute("UPDATE referral_links SET title=? WHERE code=?", (title, code))


def update_referral_description(code: str, description: str):
    cur.execute("UPDATE referral_links SET description=? WHERE code=?", (description, code))


def inc_referral_click(code: str):
    cur.execute("UPDATE referral_links SET clicks=clicks+1 WHERE code=?", (code,))


def _record_referral_join(code: str, u):
    if not u:
        return
    cur.execute(
        """
    INSERT INTO referral_joins(code,user_id,username,first_name,last_name,joined_at)
    VALUES(?,?,?,?,?,?)
    """,
        (code, u.id, u.username or "", u.first_name or "", u.last_name or "", now_iso()),
    )


def list_referral_joiners(code: str, limit: int = 10):
    rows = cur.execute(
        "SELECT * FROM referral_joins WHERE code=? ORDER BY id DESC LIMIT ?",
        (code, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def inc_referral_signup(code: str, user=None):
    cur.execute("UPDATE referral_links SET signups=signups+1 WHERE code=?", (code,))
    _record_referral_join(code, user)


def save_or_update_user(u):
    cur.execute(
        """
    INSERT INTO users(user_id,username,first_name,last_name,wallet,created_at)
    VALUES(?,?,?,?,0,?)
    ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name
    """,
        (u.id, u.username or "", u.first_name or "", u.last_name or "", now_iso()),
    )


def db_get_wallet(uid: int) -> int:
    r = cur.execute("SELECT wallet FROM users WHERE user_id=?", (uid,)).fetchone()
    return r[0] if r else 0


def db_add_wallet(uid: int, amount: int):
    cur.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (amount, uid))


def try_deduct_wallet(uid: int, price: int) -> bool:
    cur.execute("BEGIN IMMEDIATE")
    r = cur.execute("SELECT wallet FROM users WHERE user_id=?", (uid,)).fetchone()
    bal = r[0] if r else 0
    if bal < price:
        cur.execute("ROLLBACK")
        return False
    cur.execute("UPDATE users SET wallet=wallet-? WHERE user_id=?", (price, uid))
    cur.execute("COMMIT")
    return True


def rollback_wallet(uid: int, price: int):
    cur.execute("BEGIN IMMEDIATE")
    cur.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (price, uid))
    cur.execute("COMMIT")


def db_new_payment(uid: int, amount: int, note: str, media: list[dict] | list[str]):
    cur.execute(
        "INSERT INTO payments(user_id,amount,note,photos_json,status,created_at) VALUES(?,?,?,?, 'pending', ?)",
        (uid, amount, note, json.dumps(media, ensure_ascii=False), now_iso()),
    )
    return cur.lastrowid


def db_list_pending_payments_page(offset: int, limit: int):
    rows = cur.execute("SELECT * FROM payments WHERE status='pending' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    total = cur.execute("SELECT COUNT(1) FROM payments WHERE status='pending'").fetchone()[0]
    return [dict(x) for x in rows], total


def db_get_payment(pid: int):
    r = cur.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def db_update_payment_status(pid: int, status: str):
    cur.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))


def db_get_plan(pid: str):
    r = cur.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def db_list_plans():
    return [dict(r) for r in cur.execute("SELECT * FROM plans ORDER BY sort_order ASC, id ASC").fetchall()]


def db_insert_plan(pid: str, title: str, days: int, gb: int, price: int, flags: dict | None = None):
    cur.execute(
        "INSERT INTO plans(id,title,days,gb,price,flags) VALUES(?,?,?,?,?,?)",
        (pid, title, int(days), int(gb), int(price), json.dumps(flags or {}, ensure_ascii=False)),
    )


def db_update_plan_field(pid: str, field: str, value):
    if field not in ("title", "days", "gb", "price", "flags"):
        raise ValueError("invalid field")
    cur.execute(f"UPDATE plans SET {field}=? WHERE id=?", (value, pid))


def db_delete_plan(pid: str):
    cur.execute("DELETE FROM plans WHERE id=?", (pid,))


def db_swap_plan_order(pid: str, direction: str):
    plans = db_list_plans()
    ordered = sorted(plans, key=lambda p: (int(p.get("sort_order") or 0), p["id"]))
    idx = next((i for i, p in enumerate(ordered) if p["id"] == pid), None)
    if idx is None:
        return False
    if direction == "up":
        target_idx = idx - 1
    elif direction == "down":
        target_idx = idx + 1
    else:
        return False
    if target_idx < 0 or target_idx >= len(ordered):
        return False
    a = ordered[idx]
    b = ordered[target_idx]
    order_a = int(a.get("sort_order") or idx)
    order_b = int(b.get("sort_order") or target_idx)
    cur.execute("UPDATE plans SET sort_order=? WHERE id=?", (order_b, a["id"]))
    cur.execute("UPDATE plans SET sort_order=? WHERE id=?", (order_a, b["id"]))
    return True


def db_get_plans_for_user(is_admin: bool):
    rows = [dict(r) for r in cur.execute("SELECT * FROM plans ORDER BY sort_order ASC, id ASC").fetchall()]
    res = []
    for p in rows:
        import json

        flags = json.loads(p.get("flags") or "{}")
        if flags.get("admin_only") and not is_admin:
            continue
        res.append(p)
    return res


def user_has_test_purchase(uid: int) -> bool:
    """
    Detect if user has ever received a test plan.
    Uses LEFT JOIN so even deleted plans are considered, and also inspects purchase.meta.
    """
    rows = cur.execute(
        """
        SELECT p.plan_id, p.meta, COALESCE(pl.flags, '{}') AS flags
        FROM purchases p
        LEFT JOIN plans pl ON pl.id = p.plan_id
        WHERE p.user_id=?
        """,
        (uid,),
    ).fetchall()
    for r in rows:
        try:
            flags = json.loads(r["flags"] or "{}")
        except Exception:
            flags = {}
        if flags.get("test"):
            return True
        meta = r["meta"]
        if meta:
            try:
                m = json.loads(meta)
                if isinstance(m, dict) and m.get("test"):
                    return True
            except Exception:
                if str(meta).lower() == "test":
                    return True
    return False


def db_new_purchase(**kw):
    fields = [
        "user_id",
        "plan_id",
        "price",
        "three_xui_client_id",
        "three_xui_inbound_id",
        "client_email",
        "sub_id",
        "sub_link",
        "allocated_gb",
        "expiry_ms",
        "created_at",
        "meta",
        "active",
        "superseded_by",
    ]
    cur.execute(
        f"INSERT INTO purchases ({','.join(fields)}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            kw.get("user_id"),
            kw.get("plan_id"),
            kw.get("price"),
            kw.get("three_xui_client_id"),
            kw.get("three_xui_inbound_id"),
            kw.get("client_email"),
            kw.get("sub_id"),
            kw.get("sub_link"),
            kw.get("allocated_gb"),
            kw.get("expiry_ms"),
            now_iso(),
            kw.get("meta"),
            int(kw.get("active")) if kw.get("active") is not None else 1,
            kw.get("superseded_by"),
        ),
    )
    return cur.lastrowid


def mark_purchase_superseded(old_id: int, new_id: int):
    cur.execute("UPDATE purchases SET active=0, superseded_by=? WHERE id=?", (new_id, old_id))


def get_active_purchase_for_inbound(uid: int, inbound_id: int | str, now_ms: int | None = None):
    """
    Active purchase rule:
      - active=1 AND inbound matches AND (expiry_ms is null/zero OR expiry_ms > now)
      - expired purchases (expiry_ms <= now) are treated as inactive for renewals, and a NEW client is created.
    """
    if now_ms is None:
        now_ms = int(datetime.now(TZ).timestamp() * 1000)
    r = cur.execute(
        """
        SELECT * FROM purchases
        WHERE user_id=? AND active=1 AND three_xui_inbound_id=? AND (expiry_ms IS NULL OR expiry_ms=0 OR expiry_ms>?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, str(inbound_id), now_ms),
    ).fetchone()
    return dict(r) if r else None


def list_active_purchases(now_ms: int | None = None, inbound_id: int | str | None = None):
    if now_ms is None:
        now_ms = int(datetime.now(TZ).timestamp() * 1000)
    q = "SELECT * FROM purchases WHERE active=1 AND (expiry_ms IS NULL OR expiry_ms=0 OR expiry_ms>?)"
    params: list = [now_ms]
    if inbound_id is not None:
        q += " AND three_xui_inbound_id=?"
        params.append(str(inbound_id))
    return [dict(r) for r in cur.execute(q, params).fetchall()]


def user_purchases(uid: int):
    return [dict(r) for r in cur.execute("SELECT * FROM purchases WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()]


def user_active_purchases(uid: int):
    return [
        dict(r)
        for r in cur.execute(
            "SELECT * FROM purchases WHERE user_id=? AND active=1 ORDER BY id DESC",
            (uid,),
        ).fetchall()
    ]


def cache_set_usage(purchase_id: int, up: int, down: int, total: int, expiry_ms: int):
    cur.execute(
        "INSERT OR REPLACE INTO cache_usage(purchase_id,up,down,total,expiry_ms,updated_at) VALUES(?,?,?,?,?,?)",
        (purchase_id, up, down, total, expiry_ms, now_iso()),
    )


def cache_get_usage(purchase_id: int):
    r = cur.execute("SELECT * FROM cache_usage WHERE purchase_id=?", (purchase_id,)).fetchone()
    return dict(r) if r else None


def get_or_open_ticket(uid: int) -> int:
    row = cur.execute("SELECT id FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO tickets(user_id,status,opened_at,closed_at,last_activity) VALUES (?,?,?,?,?)", (uid, "open", now_iso(), None, now_iso()))
    return cur.lastrowid


def ticket_set_activity(tid: int):
    cur.execute("UPDATE tickets SET last_activity=? WHERE id=?", (now_iso(), tid))


def ticket_close(tid: int):
    cur.execute("UPDATE tickets SET status='closed', closed_at=?, last_activity=? WHERE id=?", (now_iso(), now_iso(), tid))


def store_tmsg(
    tid: int,
    sender_type: str,
    sender_id: int,
    kind: str,
    content: str | None,
    caption: str | None,
    tg_msg_id: int | None,
    src_chat_id: int | None = None,
    src_message_id: int | None = None,
):
    try:
        cur.execute(
            """
        INSERT INTO ticket_messages(ticket_id,sender_type,sender_id,kind,content,caption,tg_msg_id,created_at,src_chat_id,src_message_id)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
            (tid, sender_type, sender_id, kind, content, caption, tg_msg_id, now_iso(), src_chat_id, src_message_id),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # Duplicate source message, ignore
        return None


def list_tickets_page(page: int, size: int):
    off = page * size
    rows = [
        dict(r)
        for r in cur.execute(
            """
        SELECT t.*, u.username, u.first_name, u.last_name
        FROM tickets t
        LEFT JOIN users u ON u.user_id = t.user_id
        ORDER BY (t.status='open') DESC, t.last_activity DESC
        LIMIT ? OFFSET ?
    """,
            (size, off),
        ).fetchall()
    ]
    total = cur.execute("SELECT COUNT(1) FROM tickets").fetchone()[0]
    return rows, total


def list_ticket_messages_page(tid: int, page: int, size: int):
    off = page * size
    rows = [
        dict(r)
        for r in cur.execute(
            """
            SELECT
                id,
                ticket_id,
                sender_type AS sender_role,
                content AS text,
                caption AS media_json,
                created_at,
                src_chat_id,
                src_message_id,
                kind,
                sender_id
            FROM ticket_messages
            WHERE ticket_id=?
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (tid, size, off),
        ).fetchall()
    ]
    total = cur.execute("SELECT COUNT(1) FROM ticket_messages WHERE ticket_id=?", (tid,)).fetchone()[0]
    return rows, total


def find_ticket_by_msg_id(tg_msg_id: int):
    r = cur.execute("SELECT ticket_id FROM ticket_messages WHERE tg_msg_id=?", (tg_msg_id,)).fetchone()
    return r["ticket_id"] if r else None


def get_global_discount_percent() -> int:
    try:
        return max(0, min(90, int(str(get_setting("GLOBAL_DISCOUNT_PERCENT", "0")).strip() or 0)))
    except Exception:
        return 0


def purchases_stats_range(start_iso: str, end_iso: str):
    row = cur.execute(
        """
        SELECT COALESCE(SUM(price),0) AS revenue, COUNT(1) AS orders, COUNT(DISTINCT user_id) AS buyers
        FROM purchases
        WHERE created_at>=? AND created_at<?
        """,
        (start_iso, end_iso),
    ).fetchone()
    return {"revenue": int(row["revenue"] or 0), "orders": int(row["orders"] or 0), "buyers": int(row["buyers"] or 0)}


def events_count(event: str, start_iso: str, end_iso: str) -> int:
    row = cur.execute(
        "SELECT COUNT(1) FROM events WHERE event=? AND created_at>=? AND created_at<?",
        (event, start_iso, end_iso),
    ).fetchone()
    return int(row[0] or 0)
