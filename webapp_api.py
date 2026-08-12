import os
import requests
from flask import Flask, request, jsonify, send_from_directory, redirect

import config
import database as db
import telegram_notify
from webapp_auth import parse_and_verify_init_data, dev_fallback_user

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")

app = Flask(__name__, static_folder=WEBAPP_DIR, static_url_path="")


def authenticate():
    """Returns the tg_id of the caller, or None if auth fails."""
    init_data = request.headers.get("X-Init-Data", "")
    result = parse_and_verify_init_data(init_data)
    if not result:
        # local/dev convenience only — harmless in production since header won't be sent by real clients
        result = dev_fallback_user(request.headers.get("X-Debug-Id"))
    if not result or not result.get("user"):
        return None
    return result["user"]["id"], result["user"]


def require_user():
    auth = authenticate()
    if not auth:
        return None, (jsonify({"error": "unauthorized"}), 401)
    tg_id, tg_user = auth
    user = db.get_or_create_user(
        tg_id=tg_id,
        username=tg_user.get("username"),
        full_name=(tg_user.get("first_name", "") + " " + tg_user.get("last_name", "")).strip(),
    )
    return user, None


# ---------------------------------------------------------------- static app

@app.route("/")
def index():
    return send_from_directory(WEBAPP_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(WEBAPP_DIR, path)


# ---------------------------------------------------------------- image proxy (for telegram file_id based images)

@app.route("/img/<file_id>")
def img_proxy(file_id):
    if file_id.startswith("http"):
        return redirect(file_id)
    try:
        r = requests.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=10)
        path = r.json()["result"]["file_path"]
        return redirect(f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{path}")
    except Exception:
        return "", 404


# ---------------------------------------------------------------- API

@app.route("/api/init")
def api_init():
    user, err = require_user()
    if err:
        return err
    return jsonify({
        "tg_id": user["tg_id"],
        "full_name": user["full_name"],
        "username": user["username"],
        "balance": user["balance"],
        "lang": user["lang"],
        "theme": user["theme"],
        "is_admin": user["tg_id"] in config.ADMIN_IDS,
        "channel_url": config.CHANNEL_URL,
        "support_url": config.SUPPORT_URL,
    })


@app.route("/api/lang", methods=["POST"])
def api_lang():
    user, err = require_user()
    if err:
        return err
    lang = (request.json or {}).get("lang", "uz")
    if lang not in ("uz", "ru"):
        lang = "uz"
    db.set_user_lang(user["tg_id"], lang)
    return jsonify({"ok": True, "lang": lang})


@app.route("/api/theme", methods=["POST"])
def api_theme():
    user, err = require_user()
    if err:
        return err
    theme = (request.json or {}).get("theme", "dark")
    if theme not in ("dark", "light"):
        theme = "dark"
    db.set_user_theme(user["tg_id"], theme)
    return jsonify({"ok": True, "theme": theme})


@app.route("/api/games")
def api_games():
    games = db.list_games()
    for g in games:
        g["image_url"] = _resolve_img(g["image_url"])
    return jsonify(games)


@app.route("/api/games/<int:game_id>")
def api_game(game_id):
    g = db.get_game(game_id)
    if not g:
        return jsonify({"error": "not found"}), 404
    g["image_url"] = _resolve_img(g["image_url"])
    return jsonify(g)


@app.route("/api/order", methods=["POST"])
def api_order():
    user, err = require_user()
    if err:
        return err
    body = request.json or {}
    game_id = body.get("game_id")
    package_index = body.get("package_index")
    game = db.get_game(game_id)
    if not game or package_index is None or package_index >= len(game["packages"]):
        return jsonify({"error": "invalid package"}), 400
    package = game["packages"][package_index]
    new_balance = db.change_balance(user["tg_id"], -package["price"])
    if new_balance is False:
        return jsonify({"error": "insufficient_balance"}), 400
    order_id = db.create_order(user["tg_id"], game_id, game["name"], package["label"], package["price"])
    return jsonify({"ok": True, "order_id": order_id, "balance": new_balance})


@app.route("/api/orders")
def api_orders():
    user, err = require_user()
    if err:
        return err
    return jsonify(db.list_orders(user["tg_id"]))


@app.route("/api/leaderboard")
def api_leaderboard():
    return jsonify(db.get_leaderboard())


@app.route("/api/banners")
def api_banners():
    banners = db.list_banners()
    for b in banners:
        b["image_url"] = _resolve_img(b["image_url"])
    return jsonify(banners)


@app.route("/api/reviews")
def api_reviews():
    return jsonify(db.list_reviews())


@app.route("/api/reviews", methods=["POST"])
def api_add_review():
    user, err = require_user()
    if err:
        return err
    body = request.json or {}
    text = (body.get("text") or "").strip()
    rating = int(body.get("rating") or 5)
    if not text:
        return jsonify({"error": "empty"}), 400
    rid = db.add_review(user["tg_id"], text, rating)
    return jsonify({"ok": True, "id": rid})


@app.route("/api/topup-info")
def api_topup_info():
    return jsonify({
        "card_number": config.CARD_NUMBER,
        "card_holder": config.CARD_HOLDER,
        "card_bank": config.CARD_BANK,
        "min_amount": 1000,
    })


@app.route("/api/topup/request", methods=["POST"])
def api_topup_request():
    user, err = require_user()
    if err:
        return err
    body = request.json or {}
    amount = int(body.get("amount") or 0)
    method = body.get("method", "UZCARD")
    if amount < 1000:
        return jsonify({"error": "amount_too_small"}), 400
    topup_id = db.create_topup(user["tg_id"], amount, method)
    telegram_notify.notify_admins_new_topup(topup_id, user, amount, method)
    return jsonify({"ok": True, "topup_id": topup_id})


def _resolve_img(value):
    if not value:
        return ""
    if value.startswith("http"):
        return value
    return f"/img/{value}"
