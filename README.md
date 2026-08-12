# Yopish — Telegram bot + Mini App

To'liq ishlaydigan Telegram bot + Mini App (Web App): o'yin valyutasi (UC va h.k.) sotib olish, balans to'ldirish, top xaridorlar, sharhlar va admin panel.

## Tuzilma

```
main.py             - ishga tushirish nuqtasi (bot + web server)
config.py            - sozlamalar (.env orqali)
database.py           - SQLite bazasi
webapp_api.py          - Flask API + Mini App'ni serve qilish
webapp_auth.py          - Telegram WebApp initData tekshiruvi
telegram_notify.py       - adminlarga xabar yuborish (HTTP orqali)
bot/handlers_user.py      - /start
bot/handlers_admin.py      - /admin va uning barcha funksiyalari
webapp/               - Mini App (HTML/CSS/JS)
```

## 1. Bot yaratish

1. Telegramda **@BotFather** ga boring, `/newbot` bilan bot yarating, tokenni oling.
2. Botga Mini App qo'shish shart emas — biz `WebAppInfo` orqali tugma yasaymiz, lekin xohlasangiz `/newapp` bilan ham ro'yxatdan o'tkazing.
3. Bot sozlamalarida (BotFather → Bot Settings → Menu Button) Mini App URL'ini qo'yib qo'yishingiz mumkin (ixtiyoriy).

## 2. Lokal ishga tushirish

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# .env faylini oching va BOT_TOKEN, ADMIN_IDS va h.k. ni to'ldiring
python main.py
```

Server `http://localhost:10000` da ishga tushadi. Mini App'ni brauzerda test qilish uchun `?debug_id=123456` parametrini qo'shing (Telegram tashqarisida ishlatish uchun, productionda kerak emas).

## 3. Render.com ga joylashtirish

1. Ushbu papkani GitHub'ga push qiling.
2. Render.com da **New → Web Service** tanlang, repo'ni ulang.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python main.py`
5. Quyidagi Environment Variable'larni qo'shing:
   - `BOT_TOKEN` — BotFather'dan olingan token
   - `ADMIN_IDS` — sizning Telegram ID'ingiz (bir nechta bo'lsa vergul bilan: `111,222`)
   - `WEBAPP_URL` — Render bergan URL, masalan `https://sizning-app.onrender.com`
   - `CHANNEL_URL`, `SUPPORT_URL` — kanal va yordam havolalari
   - `CARD_NUMBER`, `CARD_HOLDER`, `CARD_BANK` — to'lov uchun karta ma'lumotlari
   - `START_IMAGE` — /start xabaridagi rasm URL'i
6. Deploy tugmasini bosing. Deploy tugagach `WEBAPP_URL` ni haqiqiy Render domeningizga yangilang va qayta deploy qiling.

> Eslatma: Render'ning bepul tarifida servis harakatsizlikdan keyin "uxlab qoladi" — bot birinchi xabarga sekinroq javob berishi mumkin. Doimiy ishlashi uchun pullik tarif yoki UptimeRobot kabi "ping" xizmatidan foydalaning.

## 4. Foydalanish

- **Foydalanuvchi**: botga `/start` yozadi → rasm + "Ilovaga kirish" tugmasi chiqadi → Mini App ochiladi.
- **Admin**: `/admin` yozadi (faqat `ADMIN_IDS` ichidagilar uchun ishlaydi) → statistikani ko'radi, banner yangilaydi, yangi o'yin qo'shadi, foydalanuvchiga balans tashlaydi.
- **Balans to'ldirish**: foydalanuvchi Mini App'da summani kiritadi → karta raqami va 3 daqiqalik taymer chiqadi → "To'lovni amalga oshirdim" bosgach, barcha adminlarga tasdiqlash so'rovi keladi (✅/❌ tugmalar bilan) → admin tasdiqlasa, balans avtomatik qo'shiladi va foydalanuvchiga xabar boradi.

## 5. Kengaytirish g'oyalari

- To'lov tizimini (Payme/Click) avtomatlashtirish
- O'yinlarni admin panelidan o'chirish/tahrirlash
- Har bir tilga (UZ/RU) to'liq tarjima qo'shish (hozir interfeys UZ, til tugmasi foydalanuvchi tanlovini saqlaydi — matnlarni ikkinchi tilga tarjima qilish uchun `webapp/app.js` ichida oddiy lug'at qo'shsa bo'ladi)
