from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    waiting_banner_photo = State()
    waiting_new_game_name = State()
    waiting_new_game_image = State()
    waiting_new_game_package = State()
    waiting_balance_target = State()
    waiting_balance_amount = State()


class UserStates(StatesGroup):
    waiting_review_text = State()
