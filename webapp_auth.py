import hashlib
import hmac
import json
from urllib.parse import parse_qsl

import config


def parse_and_verify_init_data(init_data: str, max_age_seconds: int = None):
    """
    Validates Telegram WebApp initData.
    Returns dict with parsed user info if valid, otherwise None.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
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
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    user_raw = pairs.get("user")
    user = json.loads(user_raw) if user_raw else None
    return {"user": user, "raw": pairs}


def dev_fallback_user(tg_id_header):
    """Allows local testing without a real Telegram session, using an X-Debug-Id header."""
    if not tg_id_header:
        return None
    try:
        return {"user": {"id": int(tg_id_header), "first_name": "Test", "username": "test"}}
    except ValueError:
        return None
