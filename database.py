import sqlite3
import json
import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now():
    return datetime.datetime.utcnow().isoformat()


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            balance INTEGER NOT NULL DEFAULT 0,
            lang TEXT NOT NULL DEFAULT 'uz',
            theme TEXT NOT NULL DEFAULT 'dark',
            referred_by INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            image_url TEXT,
            shape TEXT NOT NULL DEFAULT 'rounded',
            packages TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id INTEGER,
            game_name TEXT,
            package_label TEXT,
            price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_url TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            rating INTEGER NOT NULL DEFAULT 5,
            created_at TEXT
        );
        """
    )
    conn.commit()

    # seed a few default games if table is empty
    c.execute("SELECT COUNT(*) as n FROM games")
    if c.fetchone()["n"] == 0:
        default_games = [
            ("PUBG Mobile", "https://i.imgur.com/1.png", "rounded"),
            ("Free Fire", "https://i.imgur.com/2.png", "rounded"),
            ("Mobile Legends", "https://i.imgur.com/3.png", "rounded"),
            ("Honor of Kings", "https://i.imgur.com/4.png", "rounded"),
            ("Standoff 2", "https://i.imgur.com/5.png", "rounded"),
        ]
        for name, img, shape in default_games:
            packages = json.dumps([
                {"label": "60 UC", "amount": 60, "price": 11700},
                {"label": "325 UC", "amount": 325, "price": 59000},
                {"label": "660 UC", "amount": 660, "price": 115000},
            ])
            c.execute(
                "INSERT INTO games (name, image_url, shape, packages, active, sort_order) VALUES (?,?,?,?,1,0)",
                (name, img, shape, packages),
            )
        conn.commit()
    conn.close()


# ---------- users ----------

def get_or_create_user(tg_id, username=None, full_name=None, referred_by=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    if row:
        # keep username/full_name fresh
        c.execute("UPDATE users SET username=?, full_name=? WHERE tg_id=?", (username, full_name, tg_id))
        conn.commit()
        c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = c.fetchone()
        conn.close()
        return dict(row)
    c.execute(
        "INSERT INTO users (tg_id, username, full_name, balance, lang, referred_by, created_at) VALUES (?,?,?,0,'uz',?,?)",
        (tg_id, username, full_name, referred_by, now()),
    )
    conn.commit()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = dict(c.fetchone())
    conn.close()
    return row


def get_user(tg_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_lang(tg_id, lang):
    conn = get_conn()
    conn.execute("UPDATE users SET lang=? WHERE tg_id=?", (lang, tg_id))
    conn.commit()
    conn.close()


def set_user_theme(tg_id, theme):
    conn = get_conn()
    conn.execute("UPDATE users SET theme=? WHERE tg_id=?", (theme, tg_id))
    conn.commit()
    conn.close()


def change_balance(tg_id, delta):
    """delta can be negative (spend) or positive (top up). Returns new balance or None if user missing."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    new_balance = row["balance"] + delta
    if new_balance < 0:
        conn.close()
        return False
    c.execute("UPDATE users SET balance=? WHERE tg_id=?", (new_balance, tg_id))
    conn.commit()
    conn.close()
    return new_balance


# ---------- games ----------

def list_games(active_only=True):
    conn = get_conn()
    c = conn.cursor()
    if active_only:
        c.execute("SELECT * FROM games WHERE active=1 ORDER BY sort_order ASC, id ASC")
    else:
        c.execute("SELECT * FROM games ORDER BY sort_order ASC, id ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    for r in rows:
        r["packages"] = json.loads(r["packages"] or "[]")
    return rows


def get_game(game_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM games WHERE id=?", (game_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    g = dict(row)
    g["packages"] = json.loads(g["packages"] or "[]")
    return g


def add_game(name, image_url, shape="rounded", packages=None):
    packages = packages or [
        {"label": "Standart", "amount": 1, "price": 10000},
    ]
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO games (name, image_url, shape, packages, active, sort_order) VALUES (?,?,?,?,1,999)",
        (name, image_url, shape, json.dumps(packages)),
    )
    conn.commit()
    gid = c.lastrowid
    conn.close()
    return gid


# ---------- orders ----------

def create_order(user_tg_id, game_id, game_name, package_label, price):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO orders (user_id, game_id, game_name, package_label, price, status, created_at) VALUES (?,?,?,?,?, 'completed', ?)",
        (user_tg_id, game_id, game_name, package_label, price, now()),
    )
    conn.commit()
    oid = c.lastrowid
    conn.close()
    return oid


def list_orders(user_tg_id, limit=50):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_tg_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def count_orders():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n, COALESCE(SUM(price),0) as total FROM orders")
    row = c.fetchone()
    conn.close()
    return dict(row)


# ---------- topups (balance requests) ----------

def create_topup(user_tg_id, amount, method="card"):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO topups (user_id, amount, method, status, created_at) VALUES (?,?,?, 'pending', ?)",
        (user_tg_id, amount, method, now()),
    )
    conn.commit()
    tid = c.lastrowid
    conn.close()
    return tid


def get_topup(topup_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def set_topup_status(topup_id, status):
    conn = get_conn()
    conn.execute("UPDATE topups SET status=?, processed_at=? WHERE id=?", (status, now(), topup_id))
    conn.commit()
    conn.close()


# ---------- banners ----------

def list_banners():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM banners WHERE active=1 ORDER BY sort_order ASC, id ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def add_banner(image_url):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO banners (image_url, sort_order, active) VALUES (?, 0, 1)", (image_url,))
    conn.commit()
    bid = c.lastrowid
    conn.close()
    return bid


def clear_banners():
    conn = get_conn()
    conn.execute("UPDATE banners SET active=0")
    conn.commit()
    conn.close()


# ---------- reviews ----------

def list_reviews(limit=50):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """SELECT reviews.*, users.full_name, users.username FROM reviews
           JOIN users ON users.tg_id = reviews.user_id
           ORDER BY reviews.id DESC LIMIT ?""",
        (limit,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def add_review(user_tg_id, text, rating=5):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reviews (user_id, text, rating, created_at) VALUES (?,?,?,?)",
        (user_tg_id, text[:500], rating, now()),
    )
    conn.commit()
    rid = c.lastrowid
    conn.close()
    return rid


# ---------- leaderboard / stats ----------

def get_leaderboard(limit=50):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """SELECT users.tg_id, users.full_name, users.username,
                  COALESCE(SUM(orders.price),0) as total_spent,
                  COUNT(orders.id) as order_count
           FROM users
           LEFT JOIN orders ON orders.user_id = users.tg_id AND orders.status='completed'
           GROUP BY users.tg_id
           HAVING total_spent > 0
           ORDER BY total_spent DESC
           LIMIT ?""",
        (limit,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_stats():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM users")
    users_n = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n, COALESCE(SUM(price),0) as total FROM orders")
    orders_row = dict(c.fetchone())
    c.execute("SELECT COUNT(*) as n, COALESCE(SUM(amount),0) as total FROM topups WHERE status='approved'")
    topups_row = dict(c.fetchone())
    c.execute("SELECT COUNT(*) as n FROM topups WHERE status='pending'")
    pending_n = c.fetchone()["n"]
    conn.close()
    return {
        "users": users_n,
        "orders": orders_row["n"],
        "orders_total": orders_row["total"],
        "topups_total": topups_row["total"],
        "pending_topups": pending_n,
    }
