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
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x]
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.onrender.com")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/your_channel")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/your_support")
CARD_NUMBER = os.getenv("CARD_NUMBER", "5614 6851 0539 9864")
CARD_HOLDER = os.getenv("CARD_HOLDER", "Card Holder")
CARD_BANK = os.getenv("CARD_BANK", "UZCARD")
START_IMAGE = os.getenv("START_IMAGE", "")
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
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, game_id INTEGER,
            game_name TEXT, package_label TEXT, price INTEGER NOT NULL,
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
    """)
    conn.commit()
    c.execute("SELECT COUNT(*) n FROM games")
    if c.fetchone()["n"] == 0:
        for name in ["PUBG Mobile", "Free Fire", "Mobile Legends", "Honor of Kings", "Standoff 2"]:
            pkgs = json.dumps([
                {"label": "60 UC", "price": 11700},
                {"label": "325 UC", "price": 59000},
                {"label": "660 UC", "price": 115000},
            ])
            c.execute("INSERT INTO games (name, image_url, packages) VALUES (?,?,?)", (name, "", pkgs))
        conn.commit()
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


def add_game(name, image_url, packages):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO games (name, image_url, packages, sort_order) VALUES (?,?,?,999)",
               (name, image_url, json.dumps(packages)))
    conn.commit(); gid = c.lastrowid; conn.close(); return gid


def create_order(user_tg_id, game_id, game_name, package_label, price):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO orders (user_id, game_id, game_name, package_label, price, created_at) VALUES (?,?,?,?,?,?)",
               (user_tg_id, game_id, game_name, package_label, price, now()))
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


@flask_app.route("/api/order", methods=["POST"])
def api_order():
    user, err = require_user()
    if err: return err
    body = request.json or {}
    game = get_game(body.get("game_id"))
    idx = body.get("package_index")
    if not game or idx is None or idx >= len(game["packages"]):
        return jsonify({"error": "invalid package"}), 400
    package = game["packages"][idx]
    nb = change_balance(user["tg_id"], -package["price"])
    if nb is False:
        return jsonify({"error": "insufficient_balance"}), 400
    oid = create_order(user["tg_id"], game["id"], game["name"], package["label"], package["price"])
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


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


# ============================================================================
# TELEGRAM BOT
# ============================================================================

class AdminStates(StatesGroup):
    waiting_banner_photo = State()
    waiting_new_game_name = State()
    waiting_new_game_image = State()
    waiting_new_game_package = State()
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
        [InlineKeyboardButton(text="🎮 Yangi o'yin qo'shish", callback_data="adm_newgame")],
        [InlineKeyboardButton(text="💸 Pul tashlash", callback_data="adm_addbalance")],
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
    if START_IMAGE:
        try:
            await message.answer_photo(START_IMAGE, caption=text, reply_markup=start_kb())
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
    await state.set_state(AdminStates.waiting_new_game_package)
    await message.answer(
        "📦 Paketlarni kiriting. Format: <code>Nomi-Narxi</code>, har biri yangi qatorda.\n\n"
        "Masalan:\n60 UC-11700\n325 UC-59000\n660 UC-115000",
        reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.waiting_new_game_package, F.text)
async def adm_newgame_packages(message: Message, state: FSMContext):
    data = await state.get_data()
    packages = []
    for line in message.text.strip().splitlines():
        line = line.strip()
        if not line or "-" not in line: continue
        label, _, price = line.rpartition("-")
        price = "".join(ch for ch in price if ch.isdigit())
        if not price: continue
        packages.append({"label": label.strip(), "price": int(price)})
    if not packages:
        await message.answer("❌ Format noto'g'ri. Masalan: 60 UC-11700")
        return
    add_game(data["name"], data["image"], packages)
    await state.clear()
    await message.answer(f"✅ \"{data['name']}\" qo'shildi ({len(packages)} ta paket).", reply_markup=admin_menu_kb())


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
    await message.answer(f"💰 {user.get('full_name') or user['tg_id']} uchun nechi pul tashlaysiz?")


@router.message(AdminStates.waiting_balance_amount, F.text)
async def adm_addbalance_amount(message: Message, state: FSMContext, bot: Bot):
    amount_text = "".join(ch for ch in message.text if ch.isdigit())
    if not amount_text:
        await message.answer("❌ Iltimos faqat raqam kiriting.")
        return
    amount = int(amount_text)
    data = await state.get_data()
    nb = change_balance(data["target_tg_id"], amount)
    await state.clear()
    if nb is False or nb is None:
        await message.answer("❌ Xatolik yuz berdi."); return
    await message.answer(f"✅ {data['target_name']} balansiga {amount:,} so'm qo'shildi. Yangi balans: {nb:,}".replace(",", " "),
                          reply_markup=admin_menu_kb())
    try:
        await bot.send_message(data["target_tg_id"], f"💰 Balansingizga admin {amount:,} so'm tashlab berdi!\nYangi balans: {nb:,} so'm".replace(",", " "))
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
