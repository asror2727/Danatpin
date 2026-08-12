import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Comma separated telegram user ids who can use /admin, e.g. "123456,654321"
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x]

# Public URL of the deployed web app (Render URL), used as the Telegram WebApp button target
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.onrender.com")

# Telegram channel / support links used on the /start screen and inside the app
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/your_channel")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/your_support")

# Card shown to users on the "top up" screen (admin can change this later inside settings table)
CARD_NUMBER = os.getenv("CARD_NUMBER", "5614 6851 0539 9864")
CARD_HOLDER = os.getenv("CARD_HOLDER", "Card Holder")
CARD_BANK = os.getenv("CARD_BANK", "UZCARD")

# Welcome image shown on /start (any public image url works, or a local file path starting with "file:")
START_IMAGE = os.getenv("START_IMAGE", "https://i.imgur.com/8Km9tLL.png")

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))

PORT = int(os.getenv("PORT", "10000"))
