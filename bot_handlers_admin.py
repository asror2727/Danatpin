import json
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import config
import database as db
from bot_states import AdminStates
from bot_keyboards import admin_menu_kb, cancel_kb

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS


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
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    s = db.get_stats()
    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👤 Foydalanuvchilar: {s['users']}\n"
        f"🧾 Buyurtmalar: {s['orders']} (jami {s['orders_total']:,} so'm)\n"
        f"💳 Tasdiqlangan to'ldirishlar: {s['topups_total']:,} so'm\n"
        f"⏳ Kutilayotgan to'lovlar: {s['pending_topups']}\n"
    ).replace(",", " ")
    await call.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "adm_orders")
async def adm_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    s = db.count_orders()
    text = f"🧾 Jami buyurtmalar soni: <b>{s['n']}</b>\nJami summa: <b>{s['total']:,}</b> so'm".replace(",", " ")
    await call.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()


# ---------- banner update flow ----------

@router.callback_query(F.data == "adm_banner")
async def adm_banner_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_banner_photo)
    await call.message.edit_text(
        "🖼 Yangi banner rasmini yuboring (1 tadan bir nechtagacha, ketma-ket yuborishingiz mumkin).\n"
        "Tugatgach /done deb yozing.",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(AdminStates.waiting_banner_photo, F.photo)
async def adm_banner_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    db.add_banner(file_id)
    await message.answer("✅ Banner qo'shildi. Yana rasm yuboring yoki /done deb yozing.")


@router.message(AdminStates.waiting_banner_photo, Command("done"))
async def adm_banner_done(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Bannerlar yangilandi.", reply_markup=admin_menu_kb())


# ---------- new game flow ----------

@router.callback_query(F.data == "adm_newgame")
async def adm_newgame_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_new_game_name)
    await call.message.edit_text("🎮 Yangi o'yin nomini kiriting:", reply_markup=cancel_kb())
    await call.answer()


@router.message(AdminStates.waiting_new_game_name, F.text)
async def adm_newgame_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminStates.waiting_new_game_image)
    await message.answer("🖼 Endi o'yin uchun rasm yuboring (surat sifatida):", reply_markup=cancel_kb())


@router.message(AdminStates.waiting_new_game_image, F.photo)
async def adm_newgame_image(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(image=file_id)
    await state.set_state(AdminStates.waiting_new_game_package)
    await message.answer(
        "📦 Paketlarni kiriting. Format:\n<code>Nomi-Narxi</code>, har bir paket yangi qatorda.\n\n"
        "Masalan:\n60 UC-11700\n325 UC-59000\n660 UC-115000",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(AdminStates.waiting_new_game_package, F.text)
async def adm_newgame_packages(message: Message, state: FSMContext):
    data = await state.get_data()
    packages = []
    for line in message.text.strip().splitlines():
        line = line.strip()
        if not line or "-" not in line:
            continue
        label, _, price = line.rpartition("-")
        price = "".join(ch for ch in price if ch.isdigit())
        if not price:
            continue
        packages.append({"label": label.strip(), "amount": 0, "price": int(price)})

    if not packages:
        await message.answer("❌ Format noto'g'ri. Qaytadan urinib ko'ring, masalan: 60 UC-11700")
        return

    db.add_game(data["name"], data["image"], "rounded", packages)
    await state.clear()
    await message.answer(f"✅ \"{data['name']}\" o'yini qo'shildi ({len(packages)} ta paket).", reply_markup=admin_menu_kb())


# ---------- add balance (Pul tashlash) flow ----------

@router.callback_query(F.data == "adm_addbalance")
async def adm_addbalance_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_balance_target)
    await call.message.edit_text(
        "🆔 Foydalanuvchining Telegram ID yoki @username'ini yuboring:",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(AdminStates.waiting_balance_target, F.text)
async def adm_addbalance_target(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = None
    if target.isdigit():
        user = db.get_user(int(target))
    if not user:
        # try to match by username manually (SQLite has no direct helper here, so scan)
        conn = db.get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=?", (target,))
        row = c.fetchone()
        conn.close()
        user = dict(row) if row else None

    if not user:
        await message.answer("❌ Bunday foydalanuvchi topilmadi. Foydalanuvchi botga /start bergan bo'lishi kerak.")
        return

    await state.update_data(target_tg_id=user["tg_id"], target_name=user.get("full_name") or user.get("username") or user["tg_id"])
    await state.set_state(AdminStates.waiting_balance_amount)
    await message.answer(f"💰 {user.get('full_name') or user['tg_id']} uchun nechi pul tashlaysiz? (so'mda, faqat raqam)")


@router.message(AdminStates.waiting_balance_amount, F.text)
async def adm_addbalance_amount(message: Message, state: FSMContext, bot: Bot):
    amount_text = "".join(ch for ch in message.text if ch.isdigit())
    if not amount_text:
        await message.answer("❌ Iltimos faqat raqam kiriting.")
        return
    amount = int(amount_text)
    data = await state.get_data()
    target_tg_id = data["target_tg_id"]

    new_balance = db.change_balance(target_tg_id, amount)
    await state.clear()

    if new_balance is False or new_balance is None:
        await message.answer("❌ Xatolik yuz berdi.")
        return

    await message.answer(
        f"✅ {data['target_name']} balansiga {amount:,} so'm qo'shildi. Yangi balans: {new_balance:,} so'm".replace(",", " "),
        reply_markup=admin_menu_kb(),
    )
    try:
        await bot.send_message(
            target_tg_id,
            f"💰 Sizning balansingizga admin tomonidan {amount:,} so'm tashlab berildi!\nYangi balans: {new_balance:,} so'm".replace(",", " "),
        )
    except Exception:
        pass


# ---------- self-service top-up approval (triggered from the web app) ----------

@router.callback_query(F.data.startswith("tu_ok_"))
async def topup_approve(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    topup_id = int(call.data.split("_")[-1])
    topup = db.get_topup(topup_id)
    if not topup or topup["status"] != "pending":
        await call.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True)
        return
    new_balance = db.change_balance(topup["user_id"], topup["amount"])
    db.set_topup_status(topup_id, "approved")
    await call.message.edit_text(call.message.text + "\n\n✅ Tasdiqlandi")
    try:
        await bot.send_message(
            topup["user_id"],
            f"✅ To'lovingiz tasdiqlandi! Balansingizga {topup['amount']:,} so'm qo'shildi.\nYangi balans: {new_balance:,} so'm".replace(",", " "),
        )
    except Exception:
        pass
    await call.answer("Tasdiqlandi ✅")


@router.callback_query(F.data.startswith("tu_no_"))
async def topup_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruhsat yo'q", show_alert=True)
        return
    topup_id = int(call.data.split("_")[-1])
    topup = db.get_topup(topup_id)
    if not topup or topup["status"] != "pending":
        await call.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True)
        return
    db.set_topup_status(topup_id, "rejected")
    await call.message.edit_text(call.message.text + "\n\n❌ Rad etildi")
    try:
        await bot.send_message(topup["user_id"], "❌ To'lovingiz tasdiqlanmadi. Agar xato bo'lsa, Yordam bo'limiga murojaat qiling.")
    except Exception:
        pass
    await call.answer("Rad etildi ❌")
