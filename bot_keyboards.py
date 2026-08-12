from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
import config


def start_kb():
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Ilovaga kirish", web_app=WebAppInfo(url=config.WEBAPP_URL))],
            [InlineKeyboardButton(text="📢 Kanal", url=config.CHANNEL_URL)],
            [InlineKeyboardButton(text="🆘 Yordam", url=config.SUPPORT_URL)],
        ]
    )
    return kb


def admin_menu_kb():
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
            [InlineKeyboardButton(text="🧾 Jami buyurtmalar", callback_data="adm_orders")],
            [InlineKeyboardButton(text="🖼 Reklama banner yangilash", callback_data="adm_banner")],
            [InlineKeyboardButton(text="🎮 Yangi o'yin qo'shish", callback_data="adm_newgame")],
            [InlineKeyboardButton(text="💸 Pul tashlash", callback_data="adm_addbalance")],
        ]
    )
    return kb


def cancel_kb():
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_cancel")]]
    )
    return kb


def topup_decision_kb(topup_id):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"tu_ok_{topup_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"tu_no_{topup_id}"),
            ]
        ]
    )
    return kb
