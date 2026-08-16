import os
import json
import sqlite3
import hmac
import hashlib
import logging
import asyncio
import threading
import datetime
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify, redirect
from dotenv import load_dotenv

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

# ============================================================================
# CONFIG
# ============================================================================

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
HYPERPIN_API_URL = os.getenv("HYPERPIN_API_URL", "https://api.hyperpin.top/api/v1").rstrip("/")
HYPERPIN_API_KEY = os.getenv("HYPERPIN_API_KEY", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
PORT = int(os.getenv("PORT", "10000"))
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")

# ============================================================================
# DATABASE
# ============================================================================

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

    # --- lightweight migration: add any columns that older deployed
    # databases might be missing, so existing data.db files don't break
    # when new columns are introduced in later versions of this bot ---
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
    ])

    conn.close()


def get_or_create_user(tg_id, username=None, full_name=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE users SET username=?, full_name=? WHERE tg_id=?", (username, full_name, tg_id))
        conn.commit()
        c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = dict(c.fetchone()); conn.close(); return row
    c.execute("INSERT INTO users (tg_id, username, full_name, created_at) VALUES (?,?,?,?)",
               (tg_id, username, full_name, now()))
    conn.commit()
    c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = dict(c.fetchone()); conn.close(); return row


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


# ============================================================================
# HYPERPIN INTEGRATION
#
# HONESTY NOTE: api.hyperpin.top has no public documentation that could be
# found. The functions below use the most common pattern for this type of
# reseller top-up panel (single POST endpoint + api_key + action), but the
# exact field names (action / order / check names, param names) are NOT
# verified against the real service — this sandbox has no internet access
# to test it. Use the admin bot button "🔌 HyperPin holatini tekshirish" to
# see the RAW response HyperPin actually sends back; if it doesn't look
# right, forward that raw text and the exact field names get corrected.
# ============================================================================

def hyperpin_request(payload: dict, timeout=15):
    """Low-level POST to the HyperPin API. Returns (ok, data_or_error_text)."""
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


def get_setting(key, default=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
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


# ============================================================================
# TELEGRAM HTTP HELPERS (used from Flask thread — plain requests, no aiogram)
# ============================================================================

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


# ============================================================================
# WEBAPP AUTH (validates Telegram Mini App initData)
# ============================================================================

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


# ============================================================================
# FLASK APP (Mini App + API)
# ============================================================================

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
    return f"/img/{value}"


@flask_app.route("/")
def index():
    try:
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html topilmadi. Uni main.py bilan bir xil papkaga joylashtiring.", 500


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

    if game["hyperpin_enabled"] and package.get("hyperpin_code"):
        ok, result = hyperpin_create_order(game["hyperpin_game_code"], package["hyperpin_code"], player_id, body.get("server_id"))
        if not ok:
            # HyperPin failed — refund immediately, don't leave the user out of pocket
            nb = change_balance(user["tg_id"], package["price"])
            set_order_hyperpin(oid, None, "failed")
            tg_send_message(user["tg_id"], f"❌ Buyurtma bajarilmadi (yetkazib beruvchida xatolik), summa qaytarildi: {package['price']:,} so'm".replace(",", " "))
            for admin_id in ADMIN_IDS:
                tg_send_message(admin_id, f"⚠️ HyperPin xatosi (buyurtma #{oid}):\n<code>{json.dumps(result, ensure_ascii=False)[:800]}</code>")
            return jsonify({"error": "fulfillment_failed", "balance": nb}), 502
        hp_order_id = None
        if isinstance(result, dict) and isinstance(result.get("body"), dict):
            hp_order_id = result["body"].get("order_id") or result["body"].get("id")
        set_order_hyperpin(oid, str(hp_order_id) if hp_order_id else None, "sent")

    tg_send_message(
        user["tg_id"],
        (f"✅ Buyurtmangiz bajarildi!\n🎮 {game['name']} — {package['label']}\n"
         f"💰 {package['price']:,} so'm\n\nIltimos, ilovada xizmatimizga fikr (izoh va yulduz) qoldiring ⭐").replace(",", " "),
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


# ============================================================================
# TELEGRAM BOT
# ============================================================================

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


def is_admin(tg_id):
    return tg_id in ADMIN_IDS


def start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Ilovaga kirish", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="📢 Kanal", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="🆘 Yordam", url=SUPPORT_URL)],
    ])


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
    ])


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_cancel")]])


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = (f"Xush kelibsiz, {message.from_user.first_name}! 👋✌🏻\n\n"
            "Bu yerda siz sevimli o'yinlaringiz uchun UC, Prime va boshqa xizmatlarni "
            "eng tez va eng arzon narxlarda sotib olishingiz mumkin.\n\n"
            "Boshlash uchun pastdagi tugmalardan foydalaning 👇")
    image = get_setting("start_image") or START_IMAGE
    if image:
        try:
            await message.answer_photo(image, caption=text, reply_markup=start_kb())
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=start_kb())


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
    await message.answer("🖼 Endi o'yin uchun rasm yuboring:", reply_markup=cancel_kb())


@router.message(AdminStates.waiting_new_game_image, F.photo)
async def adm_newgame_image(message: Message, state: FSMContext):
    await state.update_data(image=message.photo[-1].file_id)
    await state.set_state(AdminStates.waiting_new_game_hyperpin)
    await message.answer(
        "🔌 Bu o'yin HyperPin orqali avtomatik yuklab berilsinmi?\n\n"
        "Agar HA bo'lsa — HyperPin'dagi o'yin kodini yuboring (masalan: <code>pubgm</code>).\n"
        "Agar YO'Q bo'lsa — <code>yoq</code> deb yozing (buyurtmalar qo'lda bajariladi).",
        reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.waiting_new_game_hyperpin, F.text)
async def adm_newgame_hyperpin(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ("yoq", "yo'q", "yoq.", "no", "-"):
        await state.update_data(hp_enabled=0, hp_code=None)
    else:
        await state.update_data(hp_enabled=1, hp_code=text)
    await state.set_state(AdminStates.waiting_new_game_package)
    hint = (
        "📦 Paketlarni kiriting, har biri yangi qatorda.\n\n"
        "Oddiy (qo'lda bajariladigan) format: <code>Nomi-Narxi</code>\n"
        "Masalan: 60 UC-11700\n\n"
    )
    data = await state.get_data()
    if data.get("hp_enabled"):
        hint += (
            "HyperPin bilan avtomatik format: <code>Nomi-Narxi-HyperPinKod</code>\n"
            "Masalan: 60 UC-11700-60uc\n"
        )
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
    await message.answer("⭐ Bu o'yin nechi yulduzli bo'lsin? (1 dan 5 gacha, masalan: 5)", reply_markup=cancel_kb())


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
        f"Natija (ok={ok}):\n<code>{json.dumps(result, ensure_ascii=False, indent=None)[:1200]}</code>\n\n"
        "Agar bu javob noto'g'ri ko'rinsa (masalan HTML sahifa yoki 404), "
        "bu xabarni to'liq nusxalab dasturchiga yuboring — endpoint nomlarini shunga qarab moslashtirish kerak."
    )
    await call.message.answer(text, parse_mode="HTML")


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
        await message.answer("❌ Bunday foydalanuvchi topilmadi. U botga /start bergan bo'lishi kerak.")
        return
    await state.update_data(target_tg_id=user["tg_id"], target_name=user.get("full_name") or user["tg_id"])
    await state.set_state(AdminStates.waiting_balance_amount)
    await message.answer(
        f"💰 {user.get('full_name') or user['tg_id']} uchun summani kiriting.\n"
        f"Qo'shish uchun: <code>5000</code>\nKamaytirish uchun: <code>-5000</code>",
        parse_mode="HTML",
    )


@router.message(AdminStates.waiting_balance_amount, F.text)
async def adm_addbalance_amount(message: Message, state: FSMContext, bot: Bot):
    raw = message.text.strip().replace(" ", "")
    negative = raw.startswith("-")
    amount_text = "".join(ch for ch in raw if ch.isdigit())
    if not amount_text:
        await message.answer("❌ Iltimos faqat raqam kiriting (masalan 5000 yoki -5000).")
        return
    amount = int(amount_text) * (-1 if negative else 1)
    data = await state.get_data()
    nb = change_balance(data["target_tg_id"], amount)
    await state.clear()
    if nb is False:
        await message.answer("❌ Balans yetarli emas, manfiy qiymat balansdan katta bo'lishi mumkin emas.", reply_markup=admin_menu_kb())
        return
    if nb is None:
        await message.answer("❌ Xatolik yuz berdi."); return
    sign = "+" if amount >= 0 else ""
    await message.answer(f"✅ {data['target_name']} balansi {sign}{amount:,} so'm o'zgardi. Yangi balans: {nb:,}".replace(",", " "),
                          reply_markup=admin_menu_kb())
    try:
        if amount >= 0:
            note = f"💰 Balansingizga admin {amount:,} so'm tashlab berdi!\nYangi balans: {nb:,} so'm".replace(",", " ")
        else:
            note = f"⚠️ Balansingizdan {abs(amount):,} so'm ayirildi.\nYangi balans: {nb:,} so'm".replace(",", " ")
        await bot.send_message(data["target_tg_id"], note)
    except Exception:
        pass


@router.callback_query(F.data.startswith("tu_ok_"))
async def topup_approve(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True); return
    tid = int(call.data.split("_")[-1])
    t = get_topup(tid)
    if not t or t["status"] != "pending":
        await call.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True); return
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
        await call.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True); return
    set_topup_status(tid, "rejected")
    await call.message.edit_text(call.message.text + "\n\n❌ Rad etildi")
    try:
        await bot.send_message(t["user_id"], "❌ To'lovingiz tasdiqlanmadi. Yordam bo'limiga murojaat qiling.")
    except Exception:
        pass
    await call.answer("Rad etildi ❌")


async def run_bot():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN yo'q — Render Environment bo'limiga qo'shing.")
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
