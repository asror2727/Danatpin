import asyncio
import logging
import threading

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
import database as db
from bot.handlers_user import router as user_router
from bot.handlers_admin import router as admin_router
from webapp_api import app as flask_app

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")


def run_flask():
    flask_app.run(host="0.0.0.0", port=config.PORT, threaded=True, use_reloader=False)


async def run_bot():
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN is not set. Add it to your environment / .env file.")
        return
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot polling started")
    await dp.start_polling(bot)


def main():
    db.init_db()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    log.info(f"Web app / API running on port {config.PORT}")

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
