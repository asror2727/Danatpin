from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, FSInputFile
import config
import database as db
from bot_keyboards import start_kb

router = Router()


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    referred_by = None
    if command and command.args and command.args.startswith("ref"):
        try:
            referred_by = int(command.args.replace("ref", ""))
        except ValueError:
            referred_by = None

    db.get_or_create_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referred_by=referred_by,
    )

    text = (
        f"Xush kelibsiz, {message.from_user.first_name}! 👋✌🏻\n\n"
        "Bu yerda siz sevimli o'yinlaringiz uchun UC, Prime va boshqa xizmatlarni "
        "eng tez va eng arzon narxlarda sotib olishingiz mumkin.\n\n"
        "Boshlash uchun pastdagi tugmalardan foydalaning 👇"
    )

    photo = config.START_IMAGE
    try:
        if photo.startswith("file:"):
            await message.answer_photo(FSInputFile(photo[5:]), caption=text, reply_markup=start_kb())
        else:
            await message.answer_photo(photo, caption=text, reply_markup=start_kb())
    except Exception:
        # fall back to plain text if the image url is unreachable
        await message.answer(text, reply_markup=start_kb())
