# ============================================================================
# MAIN.PY (Flask API + Aiogram 3 Telegram Bot)
# ============================================================================

import os
import json
import sqlite3
import hmac
import hashlib
import logging
import asyncio
import threading
import datetime
import io
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify, redirect
from dotenv import load_dotenv

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

# CONFIG[span_0](start_span)[span_0](end_span)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x]
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.onrender.com")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/your_channel")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/x7fan")
CARD_NUMBER = os.getenv("CARD_NUMBER", "5614 6851 0539 9864")
CARD_HOLDER = os.getenv("CARD_HOLDER", "Card Holder")
CARD_BANK = os.getenv("CARD_BANK", "UZCARD")
START_IMAGE = os.getenv("START_IMAGE", "")
APP_VERSION = os.getenv("APP_VERSION", "1.01")
APP_OWNER = os.getenv("APP_OWNER", "@x7fan")
REFERRAL_SIGNUP_BONUS = int(os.getenv("REFERRAL_SIGNUP_BONUS", "200"))
REFERRAL_COMMISSION_RATE = float(os.getenv("REFERRAL_COMMISSION_RATE", "0.0005")) # 0.05%
HYPERPIN_API_URL = os.getenv("HYPERPIN_API_URL", "https://api.hyperpin.top/api/v1").rstrip("/")
HYPERPIN_API_KEY = os.getenv("HYPERPIN_API_KEY", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
PORT = int(os.getenv("PORT", "10000"))
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# DATABASE[span_1](start_span)[span_1](end_span)
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def now():
    return datetime.datetime.utcnow().isoformat()

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT, full_name TEXT,
            balance INTEGER NOT NULL DEFAULT 0,
            lang TEXT NOT NULL DEFAULT 'uz',
            theme TEXT NOT NULL DEFAULT 'dark',
            referred_by INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, image_url TEXT,
            packages TEXT NOT NULL DEFAULT '[]',
            rating REAL NOT NULL DEFAULT 5.0,
            hyperpin_enabled INTEGER NOT NULL DEFAULT 0,
            hyperpin_game_code TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, game_id INTEGER,
            game_name TEXT, package_label TEXT, price INTEGER NOT NULL,
            player_id TEXT, hyperpin_order_id TEXT, hyperpin_status TEXT,
            status TEXT NOT NULL DEFAULT 'completed', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, amount INTEGER NOT NULL, method TEXT,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT, processed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_url TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, text TEXT NOT NULL,
            rating INTEGER NOT NULL DEFAULT 5, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
    """)
    conn.commit()

    def ensure_columns(table, columns):
        c.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in c.fetchall()}
        for col_name, col_def in columns:
            if col_name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
        conn.commit()

    ensure_columns("games", [
        ("rating", "REAL NOT NULL DEFAULT 5.0"),
        ("hyperpin_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("hyperpin_game_code", "TEXT"),
    ])
    ensure_columns("orders", [
        ("player_id", "TEXT"),
        ("hyperpin_order_id", "TEXT"),
        ("hyperpin_status", "TEXT"),
    ])
    ensure_columns("users", [
        ("theme", "TEXT NOT NULL DEFAULT 'dark'"),
        ("referred_by", "INTEGER"),
    ])
    conn.close()

def get_or_create_user(tg_id, username=None, full_name=None, referred_by=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE users SET username=?, full_name=? WHERE tg_id=?", (username, full_name, tg_id))
        conn.commit()
        c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = dict(c.fetchone()); conn.close(); return row
    valid_referrer = None
    if referred_by and referred_by != tg_id:
        c.execute("SELECT tg_id FROM users WHERE tg_id=?", (referred_by,))
        if c.fetchone():
            valid_referrer = referred_by
    c.execute("INSERT INTO users (tg_id, username, full_name, referred_by, created_at) VALUES (?,?,?,?,?)",
               (tg_id, username, full_name, valid_referrer, now()))
    conn.commit()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = dict(c.fetchone()); conn.close()
    if valid_referrer:
        new_balance = change_balance(valid_referrer, REFERRAL_SIGNUP_BONUS)
        tg_send_message(
            valid_referrer,
            f"🤝 Sizning taklifingiz orqali yangi foydalanuvchi qo'shildi!\n"
            f"💰 Balansingizga {REFERRAL_SIGNUP_BONUS:,} so'm bonus qo'shildi.\n"
            f"Yangi balans: {new_balance:,} so'm".replace(",", " "),
        )
    return row

def get_referral_stats(tg_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) n FROM users WHERE referred_by=?", (tg_id,))
    invited = c.fetchone()["n"]
    c.execute("""SELECT COALESCE(SUM(orders.price),0) total FROM orders
                 JOIN users ON users.tg_id = orders.user_id
                 WHERE users.referred_by=?""", (tg_id,))
    referred_sales = c.fetchone()["total"]
    conn.close()
    earned_commission = int(referred_sales * REFERRAL_COMMISSION_RATE)
    return {"invited": invited, "referred_sales": referred_sales, "earned_commission": earned_commission}

def get_user(tg_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def set_user_field(tg_id, field, value):
    conn = get_conn()
    conn.execute(f"UPDATE users SET {field}=? WHERE tg_id=?", (value, tg_id))
    conn.commit(); conn.close()

def change_balance(tg_id, delta):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    nb = row["balance"] + delta
    if nb < 0:
        conn.close(); return False
    c.execute("UPDATE users SET balance=? WHERE tg_id=?", (nb, tg_id))
    conn.commit(); conn.close(); return nb

def list_games():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM games WHERE active=1 ORDER BY sort_order, id")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    for r in rows: r["packages"] = json.loads(r["packages"] or "[]")
    return rows

def get_game(game_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM games WHERE id=?", (game_id,))
    row = c.fetchone(); conn.close()
    if not row: return None
    g = dict(row); g["packages"] = json.loads(g["packages"] or "[]")
    return g

def add_game(name, image_url, packages, rating=5.0, hyperpin_enabled=0, hyperpin_game_code=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO games (name, image_url, packages, rating, hyperpin_enabled, hyperpin_game_code, sort_order)
                 VALUES (?,?,?,?,?,?,999)""",
               (name, image_url, json.dumps(packages), rating, hyperpin_enabled, hyperpin_game_code))
    conn.commit(); gid = c.lastrowid; conn.close(); return gid

def delete_game(game_id):
    conn = get_conn()
    conn.execute("UPDATE games SET active=0 WHERE id=?", (game_id,))
    conn.commit(); conn.close()

def list_games_all():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM games WHERE active=1 ORDER BY sort_order, id")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows

def set_order_hyperpin(order_id, hyperpin_order_id, hyperpin_status, player_id=None):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET hyperpin_order_id=?, hyperpin_status=?, player_id=COALESCE(?, player_id) WHERE id=?",
        (hyperpin_order_id, hyperpin_status, player_id, order_id),
    )
    conn.commit(); conn.close()

def clear_all_reviews():
    conn = get_conn()
    conn.execute("DELETE FROM reviews")
    conn.commit(); conn.close()

# HYPERPIN API[span_2](start_span)[span_2](end_span)
def hyperpin_request(payload: dict, timeout=15):
    if not HYPERPIN_API_KEY:
        return False, "HYPERPIN_API_KEY sozlanmagan"
    try:
        r = requests.post(
            HYPERPIN_API_URL,
            json={**payload, "api_key": HYPERPIN_API_KEY},
            headers={"Authorization": f"Bearer {HYPERPIN_API_KEY}"},
            timeout=timeout,
        )
        try:
            data = r.json()
        except ValueError:
            data = {"raw_text": r.text[:500]}
        return (r.status_code < 400), {"status_code": r.status_code, "body": data}
    except Exception as e:
        return False, str(e)

def hyperpin_check_balance():
    return hyperpin_request({"action": "balance"})

def hyperpin_check_player_id(game_code, player_id, server_id=None):
    payload = {"action": "check_id", "game": game_code, "player_id": player_id}
    if server_id:
        payload["server_id"] = server_id
    return hyperpin_request(payload)

def hyperpin_create_order(game_code, hyperpin_product_code, player_id, server_id=None):
    payload = {
        "action": "create_order",
        "game": game_code,
        "product": hyperpin_product_code,
        "player_id": player_id,
    }
    if server_id:
        payload["server_id"] = server_id
    return hyperpin_request(payload)

# AUTO BACKGROUND REMOVAL (PIL FloodFill)[span_3](start_span)[span_3](end_span)
def remove_background(image_bytes: bytes, tolerance: int = 30) -> bytes:
    if not PIL_AVAILABLE:
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        w, h = img.size
        px = img.load()

        def close(c1, c2):
            return all(abs(c1[i] - c2[i]) <= tolerance for i in range(3))

        corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        bg_colors = [px[x, y][:3] for x, y in corners]

        visited = bytearray(w * h)
        stack = [c for c in corners]
        while stack:
            x, y = stack.pop()
            idx = y * w + x
            if x < 0 or y < 0 or x >= w or y >= h or visited[idx]:
                continue
            visited[idx] = 1
            r, g, b, a = px[x, y]
            if not any(close((r, g, b), bg) for bg in bg_colors):
                continue
            px[x, y] = (r, g, b, 0)
            stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])

        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
    except Exception as e:
        log.error(f"remove_background failed: {e}")
        return image_bytes

def save_processed_upload(image_bytes: bytes, prefix: str = "img") -> str:
    filename = f"{prefix}_{int(datetime.datetime.utcnow().timestamp() * 1000)}.png"
    path = os.path.join(UPLOADS_DIR, filename)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return f"local:{filename}"

def download_and_debg(file_id: str, prefix: str = "img") -> str:
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=15)
        file_path = r.json()["result"]["file_path"]
        img_resp = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=15)
        processed = remove_background(img_resp.content)
        return save_processed_upload(processed, prefix)
    except Exception as e:
        log.error(f"download_and_debg failed for {file_id}: {e}")
        return file_id

def get_setting(key, default=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit(); conn.close()

# CUSTOMIZABLE BUTTON ICONS[span_4](start_span)[span_4](end_span)
ICON_SLOTS = {
    "balance": ("💼", "Balans belgisi"),
    "kanal": ("📢", "Kanal tugmasi"),
    "yordam": ("🆘", "Yordam tugmasi"),
    "til": ("🌐", "Til tugmasi"),
    "reviews": ("💬", "Fikrlar sarlavhasi"),
    "games_header": ("🎮", "O'yinlar sarlavhasi"),
    "top_header": ("🏆", "Top sahifa sarlavhasi"),
    "orders_header": ("🧾", "Buyurtmalar sarlavhasi"),
    "leave_review": ("✍️", "Fikr qoldirish tugmasi"),
    "nav_top": ("🏆", "Pastki menyu — Top"),
    "nav_hisob": ("💳", "Pastki menyu — Hisob"),
    "nav_orders": ("🧾", "Pastki menyu — Buyurtmalar"),
    "nav_profile": ("👤", "Pastki menyu — Profil"),
    "invite": ("🤝", "Do'st taklif belgisi"),
    "topdonor": ("🏅", "Top donatlar belgisi"),
    "support2": ("🆘", "Support (profil) belgisi"),
}

def get_all_icons():
    result = {}
    for slot, (default_emoji, _label) in ICON_SLOTS.items():
        raw = get_setting(f"icon:{slot}")
        if not raw:
            result[slot] = {"type": "emoji", "value": default_emoji}
        elif raw.startswith("image:"):
            result[slot] = {"type": "image", "value": raw[len("image:"):]}
        elif raw.startswith("emoji:"):
            result[slot] = {"type": "emoji", "value": raw[len("emoji:"):]}
        else:
            result[slot] = {"type": "emoji", "value": default_emoji}
    return result

def set_icon(slot, kind, value):
    set_setting(f"icon:{slot}", f"{kind}:{value}")

def reset_icon(slot):
    conn = get_conn()
    conn.execute("DELETE FROM settings WHERE key=?", (f"icon:{slot}",))
    conn.commit(); conn.close()

def create_order(user_tg_id, game_id, game_name, package_label, price, player_id=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO orders (user_id, game_id, game_name, package_label, price, player_id, created_at) VALUES (?,?,?,?,?,?,?)",
               (user_tg_id, game_id, game_name, package_label, price, player_id, now()))
    conn.commit(); oid = c.lastrowid; conn.close(); return oid

def list_orders(user_tg_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_tg_id,))
    rows = [dict(r) for r in c.fetchall()]; conn.close(); return rows

def count_orders():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) n, COALESCE(SUM(price),0) total FROM orders")
    row = dict(c.fetchone()); conn.close(); return row

def create_topup(user_tg_id, amount, method):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO topups (user_id, amount, method, created_at) VALUES (?,?,?,?)",
               (user_tg_id, amount, method, now()))
    conn.commit(); tid = c.lastrowid; conn.close(); return tid

def get_topup(topup_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def set_topup_status(topup_id, status):
    conn = get_conn()
    conn.execute("UPDATE topups SET status=?, processed_at=? WHERE id=?", (status, now(), topup_id))
    conn.commit(); conn.close()

def list_banners():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM banners WHERE active=1 ORDER BY id")
    rows = [dict(r) for r in c.fetchall()]; conn.close(); return rows

def add_banner(image_url):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO banners (image_url) VALUES (?)", (image_url,))
    conn.commit(); bid = c.lastrowid; conn.close(); return bid

def list_reviews():
    conn = get_conn(); c = conn.cursor()
    c.execute("""SELECT reviews.*, users.full_name, users.username FROM reviews
                 JOIN users ON users.tg_id = reviews.user_id ORDER BY reviews.id DESC LIMIT 50""")
    rows = [dict(r) for r in c.fetchall()]; conn.close(); return rows

def add_review(user_tg_id, text, rating=5):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO reviews (user_id, text, rating, created_at) VALUES (?,?,?,?)",
               (user_tg_id, text[:500], rating, now()))
    conn.commit(); rid = c.lastrowid; conn.close(); return rid

def get_leaderboard():
    conn = get_conn(); c = conn.cursor()
    c.execute("""SELECT users.tg_id, users.full_name, users.username,
                        COALESCE(SUM(orders.price),0) total_spent, COUNT(orders.id) order_count
                 FROM users LEFT JOIN orders ON orders.user_id = users.tg_id
                 GROUP BY users.tg_id HAVING total_spent > 0
                 ORDER BY total_spent DESC LIMIT 50""")
    rows = [dict(r) for r in c.fetchall()]; conn.close(); return rows

def get_stats():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) n FROM users"); users_n = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) n, COALESCE(SUM(price),0) total FROM orders"); orders_row = dict(c.fetchone())
    c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) total FROM topups WHERE status='approved'"); topups_row = dict(c.fetchone())
    c.execute("SELECT COUNT(*) n FROM topups WHERE status='pending'"); pending_n = c.fetchone()["n"]
    conn.close()
    return {"users": users_n, "orders": orders_row["n"], "orders_total": orders_row["total"],
            "topups_total": topups_row["total"], "pending_topups": pending_n}

# TELEGRAM HTTP HELPERS[span_5](start_span)[span_5](end_span)
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
    except Exception:
        pass

def notify_admins_new_topup(topup_id, user, amount, method):
    kb = {"inline_keyboard": [[
        {"text": "✅ Tasdiqlash", "callback_data": f"tu_ok_{topup_id}"},
        {"text": "❌ Rad etish", "callback_data": f"tu_no_{topup_id}"},
    ]]}
    name = user.get("full_name") or user.get("username") or user["tg_id"]
    text = (f"💳 <b>Yangi to'lov so'rovi</b>\n\n👤 {name} (ID: <code>{user['tg_id']}</code>)\n"
            f"💰 Summa: {amount:,} so'm\n🏦 Usul: {method}").replace(",", " ")
    for admin_id in ADMIN_IDS:
        tg_send_message(admin_id, text, kb)

# WEBAPP AUTH[span_6](start_span)[span_6](end_span)
def parse_and_verify_init_data(init_data: str):
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    user_raw = pairs.get("user")
    return {"user": json.loads(user_raw) if user_raw else None}

def dev_fallback_user(tg_id_header):
    if not tg_id_header:
        return None
    try:
        return {"user": {"id": int(tg_id_header), "first_name": "Test", "username": "test"}}
    except ValueError:
        return None

# FLASK API[span_7](start_span)[span_7](end_span)
flask_app = Flask(__name__)

def authenticate():
    init_data = request.headers.get("X-Init-Data", "")
    result = parse_and_verify_init_data(init_data)
    if not result:
        result = dev_fallback_user(request.headers.get("X-Debug-Id"))
    if not result or not result.get("user"):
        return None
    return result["user"]["id"], result["user"]

def require_user():
    auth = authenticate()
    if not auth:
        return None, (jsonify({"error": "unauthorized"}), 401)
    tg_id, tg_user = auth
    user = get_or_create_user(
        tg_id=tg_id,
        username=tg_user.get("username"),
        full_name=(tg_user.get("first_name", "") + " " + tg_user.get("last_name", "")).strip(),
    )
    return user, None

def resolve_img(value):
    if not value:
        return ""
    if value.startswith("http"):
        return value
    if value.startswith("local:"):
        return f"/uploads/{value[len('local:'):]}"
    return f"/img/{value}"

@flask_app.route("/")
def index():
    try:
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html topilmadi.", 500

@flask_app.route("/uploads/<path:filename>")
def uploads_proxy(filename):
    safe_name = os.path.basename(filename)
    path = os.path.join(UPLOADS_DIR, safe_name)
    if not os.path.isfile(path):
        return "", 404
    from flask import send_file
    return send_file(path)

@flask_app.route("/img/<file_id>")
def img_proxy(file_id):
    if file_id.startswith("http"):
        return redirect(file_id)
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=10)
        path = r.json()["result"]["file_path"]
        return redirect(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}")
    except Exception:
        return "", 404

@flask_app.route("/api/init")
def api_init():
    user, err = require_user()
    if err: return err
    return jsonify({
        "tg_id": user["tg_id"], "full_name": user["full_name"], "username": user["username"],
        "balance": user["balance"], "lang": user["lang"], "theme": user["theme"],
        "is_admin": user["tg_id"] in ADMIN_IDS,
        "channel_url": CHANNEL_URL, "support_url": SUPPORT_URL,
        "bot_username": BOT_USERNAME, "app_version": APP_VERSION, "app_owner": APP_OWNER,
    })

@flask_app.route("/api/lang", methods=["POST"])
def api_lang():
    user, err = require_user()
    if err: return err
    lang = (request.json or {}).get("lang", "uz")
    lang = lang if lang in ("uz", "ru") else "uz"
    set_user_field(user["tg_id"], "lang", lang)
    return jsonify({"ok": True, "lang": lang})

@flask_app.route("/api/theme", methods=["POST"])
def api_theme():
    user, err = require_user()
    if err: return err
    theme = (request.json or {}).get("theme", "dark")
    theme = theme if theme in ("dark", "light") else "dark"
    set_user_field(user["tg_id"], "theme", theme)
    return jsonify({"ok": True, "theme": theme})

@flask_app.route("/api/games")
def api_games():
    games = list_games()
    for g in games: g["image_url"] = resolve_img(g["image_url"])
    return jsonify(games)

@flask_app.route("/api/hyperpin/check-id", methods=["POST"])
def api_hyperpin_check_id():
    user, err = require_user()
    if err: return err
    body = request.json or {}
    game = get_game(body.get("game_id"))
    player_id = (body.get("player_id") or "").strip()
    if not game or not player_id:
        return jsonify({"error": "invalid"}), 400
    if not game["hyperpin_enabled"] or not game["hyperpin_game_code"]:
        return jsonify({"error": "not_linked"}), 400
    ok, result = hyperpin_check_player_id(game["hyperpin_game_code"], player_id, body.get("server_id"))
    return jsonify({"ok": ok, "result": result})

@flask_app.route("/api/order", methods=["POST"])
def api_order():
    user, err = require_user()
    if err: return err
    body = request.json or {}
    game = get_game(body.get("game_id"))
    idx = body.get("package_index")
    player_id = (body.get("player_id") or "").strip() or None
    if not game or idx is None or idx >= len(game["packages"]):
        return jsonify({"error": "invalid package"}), 400
    package = game["packages"][idx]

    if game["hyperpin_enabled"] and not player_id:
        return jsonify({"error": "player_id_required"}), 400

    nb = change_balance(user["tg_id"], -package["price"])
    if nb is False:
        return jsonify({"error": "insufficient_balance"}), 400

    oid = create_order(user["tg_id"], game["id"], game["name"], package["label"], package["price"], player_id)

    if user.get("referred_by"):
        commission = int(package["price"] * REFERRAL_COMMISSION_RATE)
        if commission > 0:
            new_ref_balance = change_balance(user["referred_by"], commission)
            if new_ref_balance is not None and new_ref_balance is not False:
                tg_send_message(
                    user["referred_by"],
                    f"💸 Taklif qilgan do'stingiz xarid qildi — sizga {commission:,} so'm komissiya tushdi!".replace(",", " "),
                )

    if game["hyperpin_enabled"] and package.get("hyperpin_code"):
        ok, result = hyperpin_create_order(game["hyperpin_game_code"], package["hyperpin_code"], player_id, body.get("server_id"))
        if not ok:
            nb = change_balance(user["tg_id"], package["price"])
            set_order_hyperpin(oid, None, "failed")
            tg_send_message(user["tg_id"], f"❌ Buyurtma bajarilmadi, summa qaytarildi: {package['price']:,} so'm".replace(",", " "))
            for admin_id in ADMIN_IDS:
                tg_send_message(admin_id, f"⚠️ HyperPin xatosi (buyurtma #{oid}):\n<code>{json.dumps(result, ensure_ascii=False)[:800]}</code>")
            return jsonify({"error": "fulfillment_failed", "balance": nb}), 502
        hp_order_id = None
        if isinstance(result, dict) and isinstance(result.get("body"), dict):
            hp_order_id = result["body"].get("order_id") or result["body"].get("id")
        set_order_hyperpin(oid, str(hp_order_id) if hp_order_id else None, "sent")

    rate_kb = {"inline_keyboard": [[
        {"text": "⭐", "callback_data": f"rate_{oid}_1"},
        {"text": "⭐⭐", "callback_data": f"rate_{oid}_2"},
        {"text": "⭐⭐⭐", "callback_data": f"rate_{oid}_3"},
        {"text": "⭐⭐⭐⭐", "callback_data": f"rate_{oid}_4"},
        {"text": "⭐⭐⭐⭐⭐", "callback_data": f"rate_{oid}_5"},
    ]]}
    tg_send_message(
        user["tg_id"],
        (f"✅ Buyurtmangiz bajarildi!\n🎮 {game['name']} — {package['label']}\n"
         f"💰 {package['price']:,} so'm\n\nIltimos, xizmatimizga baho bering ⭐").replace(",", " "),
        rate_kb,
    )
    return jsonify({"ok": True, "order_id": oid, "balance": nb})

@flask_app.route("/api/orders")
def api_orders():
    user, err = require_user()
    if err: return err
    return jsonify(list_orders(user["tg_id"]))

@flask_app.route("/api/leaderboard")
def api_leaderboard():
    return jsonify(get_leaderboard())

@flask_app.route("/api/banners")
def api_banners():
    banners = list_banners()
    for b in banners: b["image_url"] = resolve_img(b["image_url"])
    return jsonify(banners)

@flask_app.route("/api/reviews", methods=["GET"])
def api_reviews_get():
    return jsonify(list_reviews())

@flask_app.route("/api/reviews", methods=["POST"])
def api_reviews_post():
    user, err = require_user()
    if err: return err
    body = request.json or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    rid = add_review(user["tg_id"], text, int(body.get("rating") or 5))
    return jsonify({"ok": True, "id": rid})

@flask_app.route("/api/icons")
def api_icons():
    icons = get_all_icons()
    for slot, data in icons.items():
        if data["type"] == "image":
            data["value"] = resolve_img(data["value"])
    return jsonify(icons)

@flask_app.route("/api/music")
def api_music():
    raw = get_setting("bg_music")
    if not raw:
        return jsonify({"url": None})
    return jsonify({"url": resolve_img(raw)})

@flask_app.route("/api/referral")
def api_referral():
    user, err = require_user()
    if err: return err
    stats = get_referral_stats(user["tg_id"])
    link = f"https://t.me/{BOT_USERNAME}?start=ref{user['tg_id']}" if BOT_USERNAME else ""
    return jsonify({
        "code": str(user["tg_id"]),
        "link": link,
        "invited": stats["invited"],
        "earned_commission": stats["earned_commission"],
        "signup_bonus": REFERRAL_SIGNUP_BONUS,
        "commission_percent": REFERRAL_COMMISSION_RATE * 100,
    })

@flask_app.route("/api/topup-info")
def api_topup_info():
    return jsonify({"card_number": CARD_NUMBER, "card_holder": CARD_HOLDER, "card_bank": CARD_BANK, "min_amount": 1000})

@flask_app.route("/api/topup/request", methods=["POST"])
def api_topup_request():
    user, err = require_user()
    if err: return err
    body = request.json or {}
    amount = int(body.get("amount") or 0)
    method = body.get("method", "UZCARD")
    if amount < 1000:
        return jsonify({"error": "amount_too_small"}), 400
    tid = create_topup(user["tg_id"], amount, method)
    notify_admins_new_topup(tid, user, amount, method)
    return jsonify({"ok": True, "topup_id": tid})

def require_admin():
    auth = authenticate()
    if not auth or auth[0] not in ADMIN_IDS:
        return None, (jsonify({"error": "forbidden"}), 403)
    return auth[0], None

@flask_app.route("/api/admin/clear-reviews", methods=["POST"])
def api_admin_clear_reviews():
    admin_id, err = require_admin()
    if err: return err
    clear_all_reviews()
    return jsonify({"ok": True})

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)

# TELEGRAM BOT HANDLERS[span_8](start_span)[span_8](end_span)
class UserStates(StatesGroup):
    waiting_review_text = State()

class AdminStates(StatesGroup):
    waiting_banner_photo = State()
    waiting_start_image = State()
    waiting_new_game_name = State()
    waiting_new_game_image = State()
    waiting_new_game_hyperpin = State()
    waiting_new_game_package = State()
    waiting_new_game_rating = State()
    waiting_balance_target = State()
    waiting_balance_amount = State()
    waiting_icon_value = State()
    waiting_music = State()

def is_admin(tg_id):
    return tg_id in ADMIN_IDS

def start_kb():
    buttons = []
    try:
        buttons.append([InlineKeyboardButton(text="🚀 Ilovaga kirish", web_app=WebAppInfo(url=WEBAPP_URL))])
    except Exception as e:
        log.error(f"start_kb: WEBAPP_URL invalid ({WEBAPP_URL}): {e}")
    try:
        buttons.append([InlineKeyboardButton(text="📢 Kanal", url=CHANNEL_URL)])
    except Exception as e:
        log.error(f"start_kb: CHANNEL_URL invalid ({CHANNEL_URL}): {e}")
    try:
        buttons.append([InlineKeyboardButton(text="🆘 Yordam", url=SUPPORT_URL)])
    except Exception as e:
        log.error(f"start_kb: SUPPORT_URL invalid ({SUPPORT_URL}): {e}")
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🧾 Jami buyurtmalar", callback_data="adm_orders")],
        [InlineKeyboardButton(text="🖼 Reklama banner yangilash", callback_data="adm_banner")],
        [InlineKeyboardButton(text="🖼 Start rasmni yangilash", callback_data="adm_startimg")],
        [InlineKeyboardButton(text="🎮 Yangi o'yin qo'shish", callback_data="adm_newgame")],
        [InlineKeyboardButton(text="🗑 O'yinlarni boshqarish", callback_data="adm_managegames")],
        [InlineKeyboardButton(text="🧹 Izohlarni tozalash", callback_data="adm_clearreviews")],
        [InlineKeyboardButton(text="💸 Pul tashlash (+/-)", callback_data="adm_addbalance")],
        [InlineKeyboardButton(text="🔌 HyperPin holatini tekshirish", callback_data="adm_hpcheck")],
        [InlineKeyboardButton(text="🖼 Tugma belgilarini boshqarish", callback_data="adm_icons")],
        [InlineKeyboardButton(text="🎵 Fon musiqasini yuklash", callback_data="adm_music")],
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_cancel")]])

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    referred_by = None
    if command and command.args and command.args.startswith("ref"):
        try:
            referred_by = int(command.args[3:])
        except ValueError:
            referred_by = None
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name, referred_by)
    text = (f"Xush kelibsiz, {message.from_user.first_name}! 👋✌🏻\n\n"
            "Bu yerda siz sevimli o'yinlaringiz uchun UC, Prime va boshqa xizmatlarni "
            "eng tez va eng arzon narxlarda sotib olishingiz mumkin.\n\n"
            "Boshlash uchun pastdagi tugmalardan foydalaning 👇")
    image = get_setting("start_image") or START_IMAGE
    kb = start_kb()
    if image:
        try:
            await message.answer_photo(image, caption=text, reply_markup=kb)
            return
        except Exception as e:
            log.error(f"cmd_start: failed to send photo ({image}): {e}")
    await message.answer(text, reply_markup=kb)

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Sizga ruhsat yo'q ❌")
        return
    await state.clear()
    await message.answer("🛠 Admin panel", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm_cancel")
async def adm_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("🛠 Admin panel", reply_markup=admin_menu_kb())
    await call.answer()

@router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    s = get_stats()
    text = (f"📊 <b>Statistika</b>\n\n👤 Foydalanuvchilar: {s['users']}\n"
            f"🧾 Buyurtmalar: {s['orders']} (jami {s['orders_total']:,} so'm)\n"
            f"💳 Tasdiqlangan to'ldirishlar: {s['topups_total']:,} so'm\n"
            f"⏳ Kutilayotgan to'lovlar: {s['pending_topups']}\n").replace(",", " ")
    await call.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "adm_orders")
async def adm_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    s = count_orders()
    text = f"🧾 Jami buyurtmalar: <b>{s['n']}</b>\nJami summa: <b>{s['total']:,}</b> so'm".replace(",", " ")
    await call.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "adm_banner")
async def adm_banner_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    await state.set_state(AdminStates.waiting_banner_photo)
    await call.message.edit_text("🖼 Yangi banner rasmini yuboring. Tugatgach /done deb yozing.", reply_markup=cancel_kb())
    await call.answer()

@router.message(AdminStates.waiting_banner_photo, F.photo)
async def adm_banner_photo(message: Message):
    add_banner(message.photo[-1].file_id)
    await message.answer("✅ Banner qo'shildi. Yana rasm yuboring yoki /done deb yozing.")

@router.message(AdminStates.waiting_banner_photo, Command("done"))
async def adm_banner_done(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Bannerlar yangilandi.", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm_startimg")
async def adm_startimg_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    await state.set_state(AdminStates.waiting_start_image)
    await call.message.edit_text("🖼 /start xabari uchun yangi rasm yuboring:", reply_markup=cancel_kb())
    await call.answer()

@router.message(AdminStates.waiting_start_image, F.photo)
async def adm_startimg_photo(message: Message, state: FSMContext):
    set_setting("start_image", message.photo[-1].file_id)
    await state.clear()
    await message.answer("✅ Start rasmi yangilandi.", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm_newgame")
async def adm_newgame_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    await state.set_state(AdminStates.waiting_new_game_name)
    await call.message.edit_text("🎮 Yangi o'yin nomini kiriting:", reply_markup=cancel_kb())
    await call.answer()

@router.message(AdminStates.waiting_new_game_name, F.text)
async def adm_newgame_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminStates.waiting_new_game_image)
    await message.answer(
        "🖼 Endi o'yin uchun rasm yuboring.\n"
        "Tavsiya: kvadrat (masalan 512x512) PNG, fon bir xil rangda bo'lsa avtomatik tozalanadi.",
        reply_markup=cancel_kb(),
    )

@router.message(AdminStates.waiting_new_game_image, F.photo)
async def adm_newgame_image(message: Message, state: FSMContext):
    processing_msg = await message.answer("⏳ Rasm qayta ishlanmoqda (fon tozalanmoqda)...")
    local_ref = download_and_debg(message.photo[-1].file_id, prefix="game")
    await state.update_data(image=local_ref)
    await processing_msg.delete()
    await state.set_state(AdminStates.waiting_new_game_hyperpin)
    await message.answer(
        "🔌 Bu o'yin HyperPin orqali avtomatik yuklab berilsinmi?\n\n"
        "Agar HA bo'lsa — HyperPin'dagi o'yin kodini yuboring (masalan: <code>pubgm</code>).\n"
        "Agar YO'Q bo'lsa — <code>yoq</code> deb yozing.",
        reply_markup=cancel_kb(), parse_mode="HTML")

@router.message(AdminStates.waiting_new_game_hyperpin, F.text)
async def adm_newgame_hyperpin(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("yoq", "yo'q", "yoq.", "no", "-"):
        await state.update_data(hp_enabled=0, hp_code=None)
    else:
        await state.update_data(hp_enabled=1, hp_code=text)
    await state.set_state(AdminStates.waiting_new_game_package)
    hint = "📦 Paketlarni kiriting, har biri yangi qatorda.\n\nFormat: <code>Nomi-Narxi</code> (Masalan: 60 UC-11700)\n"
    await message.answer(hint, reply_markup=cancel_kb(), parse_mode="HTML")

@router.message(AdminStates.waiting_new_game_package, F.text)
async def adm_newgame_packages(message: Message, state: FSMContext):
    data = await state.get_data()
    packages = []
    for line in message.text.strip().splitlines():
        line = line.strip()
        if not line or "-" not in line: continue
        parts = line.split("-")
        if data.get("hp_enabled") and len(parts) >= 3:
            label = parts[0].strip()
            price = "".join(ch for ch in parts[1] if ch.isdigit())
            hp_code = parts[2].strip()
            if not price: continue
            packages.append({"label": label, "price": int(price), "hyperpin_code": hp_code})
        else:
            label, _, price = line.rpartition("-")
            price = "".join(ch for ch in price if ch.isdigit())
            if not price: continue
            packages.append({"label": label.strip(), "price": int(price)})
    if not packages:
        await message.answer("❌ Format noto'g'ri. Masalan: 60 UC-11700")
        return
    await state.update_data(packages=packages)
    await state.set_state(AdminStates.waiting_new_game_rating)
    await message.answer("⭐ Bu o'yin nechi yulduzli bo'lsin? (1 dan 5 gacha)", reply_markup=cancel_kb())

@router.message(AdminStates.waiting_new_game_rating, F.text)
async def adm_newgame_rating(message: Message, state: FSMContext):
    try:
        rating = float(message.text.strip().replace(",", "."))
        rating = max(1.0, min(5.0, rating))
    except ValueError:
        rating = 5.0
    data = await state.get_data()
    add_game(data["name"], data["image"], data["packages"], rating,
              data.get("hp_enabled", 0), data.get("hp_code"))
    await state.clear()
    await message.answer(f"✅ \"{data['name']}\" qo'shildi ({len(data['packages'])} ta paket, ⭐{rating}).", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm_managegames")
async def adm_managegames(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    games = list_games_all()
    if not games:
        await call.message.edit_text("🗑 Hozircha o'yinlar yo'q.", reply_markup=admin_menu_kb())
        await call.answer()
        return
    rows = [[InlineKeyboardButton(text=f"❌ {g['name']}", callback_data=f"adm_delgame_{g['id']}")] for g in games]
    rows.append([InlineKeyboardButton(text="⬅ Orqaga", callback_data="adm_cancel")])
    await call.message.edit_text("🗑 O'chirish uchun o'yinni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@router.callback_query(F.data.startswith("adm_delgame_"))
async def adm_delgame(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    gid = int(call.data.split("_")[-1])
    delete_game(gid)
    await call.answer("O'chirildi ✅")
    await adm_managegames(call)

@router.callback_query(F.data == "adm_clearreviews")
async def adm_clearreviews(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    clear_all_reviews()
    await call.message.edit_text("🧹 Barcha izohlar tozalandi.", reply_markup=admin_menu_kb())
    await call.answer()

@router.callback_query(F.data == "adm_hpcheck")
async def adm_hpcheck(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    await call.answer("Tekshirilmoqda...")
    ok, result = hyperpin_check_balance()
    key_status = "✅" if HYPERPIN_API_KEY else "❌ (HYPERPIN_API_KEY yo'q)"
    text = (
        "🔌 <b>HyperPin ulanish natijasi</b>\n\n"
        f"URL: <code>{HYPERPIN_API_URL}</code>\n"
        f"Key sozlangan: {key_status}\n"
        f"Natija (ok={ok}):\n<code>{json.dumps(result, ensure_ascii=False, indent=None)[:1200]}</code>\n"
    )
    await call.message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "adm_icons")
async def adm_icons_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    icons = get_all_icons()
    rows = []
    for slot, (default_emoji, label) in ICON_SLOTS.items():
        current = icons[slot]
        shown = current["value"] if current["type"] == "emoji" else "🖼"
        rows.append([InlineKeyboardButton(text=f"{shown} {label}", callback_data=f"adm_icon_{slot}")])
    rows.append([InlineKeyboardButton(text="⬅ Orqaga", callback_data="adm_cancel")])
    await call.message.edit_text("🖼 Qaysi tugma belgisini o'zgartirasiz?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@router.callback_query(F.data.startswith("adm_icon_"))
async def adm_icon_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    slot = call.data[len("adm_icon_"):]
    default_emoji, label = ICON_SLOTS[slot]
    await state.set_state(AdminStates.waiting_icon_value)
    await state.update_data(icon_slot=slot)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Standart emojiga qaytarish", callback_data=f"adm_iconreset_{slot}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_cancel")],
    ])
    await call.message.edit_text(
        f"🖼 <b>{label}</b>\nStandart: {default_emoji}\n\n"
        "Yangi belgi uchun bitta emoji yoki rasm (PNG) yuboring. Fon avtomatik tozalanadi.",
        reply_markup=kb, parse_mode="HTML",
    )
    await call.answer()

@router.callback_query(F.data.startswith("adm_iconreset_"))
async def adm_icon_reset(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    slot = call.data[len("adm_iconreset_"):]
    reset_icon(slot)
    await state.clear()
    await call.answer("Standart holatga qaytarildi ✅")
    await adm_icons_menu(call)

@router.message(AdminStates.waiting_icon_value, F.photo)
async def adm_icon_set_image(message: Message, state: FSMContext):
    data = await state.get_data()
    slot = data.get("icon_slot")
    if not slot:
        await state.clear(); return
    processing_msg = await message.answer("⏳ Rasm qayta ishlanmoqda...")
    local_ref = download_and_debg(message.photo[-1].file_id, prefix=f"icon_{slot}")
    set_icon(slot, "image", local_ref)
    await processing_msg.delete()
    await state.clear()
    await message.answer("✅ Belgi rasm bilan yangilandi.", reply_markup=admin_menu_kb())

@router.message(AdminStates.waiting_icon_value, F.text)
async def adm_icon_set_emoji(message: Message, state: FSMContext):
    data = await state.get_data()
    slot = data.get("icon_slot")
    if not slot:
        await state.clear(); return
    value = message.text.strip()
    if len(value) > 8:
        await message.answer("❌ Iltimos bitta emoji yuboring yoki rasm yuboring.")
        return
    set_icon(slot, "emoji", value)
    await state.clear()
    await message.answer(f"✅ Belgi {value} bilan yangilandi.", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm_music")
async def adm_music_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    await state.set_state(AdminStates.waiting_music)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔇 Musiqani o'chirish", callback_data="adm_music_off")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_cancel")],
    ])
    await call.message.edit_text("🎵 Fon musiqasi uchun audio fayl yuboring (mp3, maks 3 daqiqa).", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_music_off")
async def adm_music_off(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    conn = get_conn()
    conn.execute("DELETE FROM settings WHERE key='bg_music'")
    conn.commit(); conn.close()
    await state.clear()
    await call.message.edit_text("🔇 Fon musiqasi o'chirildi.", reply_markup=admin_menu_kb())
    await call.answer()

@router.message(AdminStates.waiting_music, F.audio)
async def adm_music_set(message: Message, state: FSMContext):
    if message.audio.duration and message.audio.duration > 190:
        await message.answer("❌ Audio 3 daqiqadan uzun bo'lmasin.")
        return
    processing_msg = await message.answer("⏳ Yuklanmoqda...")
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": message.audio.file_id}, timeout=20)
        file_path = r.json()["result"]["file_path"]
        audio_resp = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30)
        filename = "bgmusic.mp3"
        with open(os.path.join(UPLOADS_DIR, filename), "wb") as f:
            f.write(audio_resp.content)
        set_setting("bg_music", f"local:{filename}")
        await processing_msg.delete()
        await state.clear()
        await message.answer("✅ Fon musiqasi o'rnatildi.", reply_markup=admin_menu_kb())
    except Exception as e:
        log.error(f"adm_music_set failed: {e}")
        await processing_msg.delete()
        await message.answer("❌ Yuklashda xatolik yuz berdi.")

@router.callback_query(F.data == "adm_addbalance")
async def adm_addbalance_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    await state.set_state(AdminStates.waiting_balance_target)
    await call.message.edit_text("🆔 Foydalanuvchining Telegram ID'ini yuboring:", reply_markup=cancel_kb())
    await call.answer()

@router.message(AdminStates.waiting_balance_target, F.text)
async def adm_addbalance_target(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = get_user(int(target)) if target.isdigit() else None
    if not user:
        conn = get_conn(); c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=?", (target,))
        row = c.fetchone(); conn.close()
        user = dict(row) if row else None
    if not user:
        await message.answer("❌ Bunday foydalanuvchi topilmadi.")
        return
    await state.update_data(target_tg_id=user["tg_id"], target_name=user.get("full_name") or user["tg_id"])
    await state.set_state(AdminStates.waiting_balance_amount)
    await message.answer(
        f"💰 {user.get('full_name') or user['tg_id']} uchun summani kiriting.\n"
        f"Qo'shish: <code>5000</code> | Kamaytirish: <code>-5000</code>",
        parse_mode="HTML",
    )

@router.message(AdminStates.waiting_balance_amount, F.text)
async def adm_addbalance_amount(message: Message, state: FSMContext, bot: Bot):
    raw = message.text.strip().replace(" ", "")
    negative = raw.startswith("-")
    amount_text = "".join(ch for ch in raw if ch.isdigit())
    if not amount_text:
        await message.answer("❌ Iltimos faqat raqam kiriting.")
        return
    amount = int(amount_text) * (-1 if negative else 1)
    data = await state.get_data()
    nb = change_balance(data["target_tg_id"], amount)
    await state.clear()
    if nb is False or nb is None:
        await message.answer("❌ Xatolik yuz berdi.", reply_markup=admin_menu_kb())
        return
    sign = "+" if amount >= 0 else ""
    await message.answer(f"✅ Balans o'zgardi: {sign}{amount:,} so'm. Yangi balans: {nb:,}".replace(",", " "), reply_markup=admin_menu_kb())
    try:
        note = f"💰 Balansingizga admin {amount:,} so'm qo'shdi!" if amount >= 0 else f"⚠️ Balansingizdan {abs(amount):,} so'm ayirildi."
        await bot.send_message(data["target_tg_id"], f"{note}\nYangi balans: {nb:,} so'm".replace(",", " "))
    except Exception:
        pass

@router.callback_query(F.data.startswith("tu_ok_"))
async def topup_approve(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    tid = int(call.data.split("_")[-1])
    t = get_topup(tid)
    if not t or t["status"] != "pending":
        await call.answer("Ko'rib chiqilgan.", show_alert=True); return
    nb = change_balance(t["user_id"], t["amount"])
    set_topup_status(tid, "approved")
    await call.message.edit_text(call.message.text + "\n\n✅ Tasdiqlandi")
    try:
        await bot.send_message(t["user_id"], f"✅ To'lovingiz tasdiqlandi! +{t['amount']:,} so'm.\nYangi balans: {nb:,} so'm".replace(",", " "))
    except Exception:
        pass
    await call.answer("Tasdiqlandi ✅")

@router.callback_query(F.data.startswith("tu_no_"))
async def topup_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    tid = int(call.data.split("_")[-1])
    t = get_topup(tid)
    if not t or t["status"] != "pending":
        await call.answer("Ko'rib chiqilgan.", show_alert=True); return
    set_topup_status(tid, "rejected")
    await call.message.edit_text(call.message.text + "\n\n❌ Rad etildi")
    try:
        await bot.send_message(t["user_id"], "❌ To'lovingiz tasdiqlanmadi.")
    except Exception:
        pass
    await call.answer("Rad etildi ❌")

@router.callback_query(F.data.startswith("rate_"))
async def on_rate_order(call: CallbackQuery, state: FSMContext):
    _, oid, stars = call.data.split("_")
    await state.set_state(UserStates.waiting_review_text)
    await state.update_data(review_order_id=int(oid), review_stars=int(stars))
    await call.message.edit_text(call.message.text + f"\n\nBahoyingiz: {'⭐' * int(stars)}")
    await call.message.answer("✍️ Endi fikringizni yozib yuboring (masalan: \"Tez va sifatli!\")")
    await call.answer()

@router.message(UserStates.waiting_review_text, F.text)
async def on_review_text(message: Message, state: FSMContext):
    data = await state.get_data()
    stars = data.get("review_stars", 5)
    text = message.text.strip()
    if not text:
        await message.answer("❌ Bo'sh xabar. Iltimos fikringizni yozing.")
        return
    add_review(message.from_user.id, text, stars)
    await state.clear()
    await message.answer(f"✅ Rahmat! Fikringiz ({'⭐' * stars}) ilovaga qo'shildi.")

async def run_bot():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN yo'q.")
        return
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot polling started")
    await dp.start_polling(bot)

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    log.info(f"Web app / API running on port {PORT}")
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
