import requests
import config

API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{API}/sendMessage", json=payload, timeout=10)
    except Exception:
        pass


def notify_admins_new_topup(topup_id, user, amount, method):
    kb = {
        "inline_keyboard": [[
            {"text": "✅ Tasdiqlash", "callback_data": f"tu_ok_{topup_id}"},
            {"text": "❌ Rad etish", "callback_data": f"tu_no_{topup_id}"},
        ]]
    }
    name = user.get("full_name") or user.get("username") or user["tg_id"]
    text = (
        "💳 <b>Yangi to'lov so'rovi</b>\n\n"
        f"👤 Foydalanuvchi: {name} (ID: <code>{user['tg_id']}</code>)\n"
        f"💰 Summa: {amount:,} so'm\n"
        f"🏦 Usul: {method}"
    ).replace(",", " ")
    for admin_id in config.ADMIN_IDS:
        send_message(admin_id, text, reply_markup=kb)
