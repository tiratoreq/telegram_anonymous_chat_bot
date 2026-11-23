from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.storage.repository import Repository

GENDER_OPTIONS = {
    "Мужчина": "male",
    "Женщина": "female",
}

PREFERRED_GENDER_OPTIONS = {
    "Любой": None,
    "Мужчина": "male",
    "Женщина": "female",
}

GENDER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=option)] for option in GENDER_OPTIONS],
    resize_keyboard=True,
    one_time_keyboard=True,
)

PREFERRED_GENDER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=option)] for option in PREFERRED_GENDER_OPTIONS],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Начать поиск")],
        [KeyboardButton(text="Изменить свои данные")],
        [KeyboardButton(text="Параметры поиска")],
    ],
    resize_keyboard=True,
)

SEARCHING_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Остановить поиск")]],
    resize_keyboard=True,
)

DIALOG_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Искать дальше")],
        [KeyboardButton(text="Прекратить диалог")],
    ],
    resize_keyboard=True,
)

POST_DIALOG_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Искать дальше")],
        [KeyboardButton(text="Назад к меню")],
    ],
    resize_keyboard=True,
)

PROFILE_CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отмена")]],
    resize_keyboard=True,
)

MIN_AGE = 13
MAX_AGE = 120


def parse_age(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text or not text.isascii() or not text.isdecimal():
        return None
    age = int(text)
    if not MIN_AGE <= age <= MAX_AGE:
        return None
    return age


class Registration(StatesGroup):
    gender = State()
    age = State()


class SearchPreferences(StatesGroup):
    gender = State()
    min_age = State()
    max_age = State()


def create_common_router(repository: Repository) -> Router:
    router = Router(name="common")

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        await repository.get_or_create_user(user_id)
        profile = await repository.get_profile(user_id)
        if profile and profile["gender"] is not None and profile["age"] is not None:
            await state.clear()
            if await repository.check_active_dialog(user_id):
                reply_markup = DIALOG_KEYBOARD
                text = "Вы уже в диалоге."
            elif await repository.check_user_in_queue(user_id):
                reply_markup = SEARCHING_KEYBOARD
                text = "Поиск уже выполняется."
            else:
                reply_markup = MAIN_KEYBOARD
                text = "Выберите действие."
            await message.answer(
                f"Ваш профиль заполнен. {text}",
                reply_markup=reply_markup,
            )
            return

        await state.set_state(Registration.gender)
        await message.answer(
            "Чтобы пользоваться поиском собеседника, сначала укажите ваш пол.",
            reply_markup=GENDER_KEYBOARD,
        )

    @router.message(Registration.gender)
    async def set_gender(message: Message, state: FSMContext) -> None:
        gender = GENDER_OPTIONS.get((message.text or "").strip())
        if gender is None:
            await message.answer(
                "Выберите пол кнопкой: «Мужчина» или «Женщина».",
                reply_markup=GENDER_KEYBOARD,
            )
            return

        await state.update_data(gender=gender)
        await state.set_state(Registration.age)
        await message.answer(
            "Теперь отправьте ваш возраст числом (от 13 до 120 лет).",
            reply_markup=PROFILE_CANCEL_KEYBOARD,
        )

    @router.message(Registration.age)
    async def set_age(message: Message, state: FSMContext) -> None:
        if (message.text or "").strip().casefold() == "отмена":
            await state.clear()
            if message.from_user is not None:
                profile = await repository.get_profile(message.from_user.id)
            else:
                profile = None
            await message.answer(
                "Изменение отменено." if profile and profile["gender"] is not None else
                "Регистрация отменена. Для заполнения профиля отправьте /start.",
                reply_markup=MAIN_KEYBOARD if profile and profile["gender"] is not None
                and profile["age"] is not None else ReplyKeyboardRemove(),
            )
            return
        age = parse_age(message.text)
        if age is None:
            await message.answer(
                "Некорректный возраст. Введите целое число от 13 до 120."
            )
            return

        data = await state.get_data()
        gender = data.get("gender")
        if gender not in {"male", "female"}:
            await state.clear()
            await message.answer("Не удалось продолжить регистрацию. Отправьте /start.")
            return

        if message.from_user is None:
            return
        await repository.save_profile(message.from_user.id, gender, age)
        await state.clear()
        await message.answer(
            "Данные сохранены. Теперь можно начать поиск.",
            reply_markup=MAIN_KEYBOARD,
        )

    @router.message(Command("profile"))
    @router.message(F.text == "Изменить свои данные")
    async def edit_profile(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        if await repository.check_active_dialog(user_id):
            await message.answer(
                "Сначала прекратите диалог, чтобы изменить свои данные.",
                reply_markup=DIALOG_KEYBOARD,
            )
            return
        if await repository.check_user_in_queue(user_id):
            await message.answer(
                "Сначала остановите поиск, чтобы изменить свои данные.",
                reply_markup=SEARCHING_KEYBOARD,
            )
            return
        await state.clear()
        await state.set_state(Registration.gender)
        await message.answer(
            "Выберите ваш пол.",
            reply_markup=GENDER_KEYBOARD,
        )

    @router.message(Command("filters"))
    @router.message(F.text == "Параметры поиска")
    async def start_search_preferences(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        profile = await repository.get_profile(message.from_user.id)
        if not profile or profile["gender"] is None or profile["age"] is None:
            await message.answer("Сначала заполните профиль: отправьте /start.")
            return

        await state.set_state(SearchPreferences.gender)
        await message.answer(
            "Какого пола искать собеседника? Выберите «Любой», «Мужчина» или «Женщина».",
            reply_markup=PREFERRED_GENDER_KEYBOARD,
        )

    @router.message(SearchPreferences.gender)
    async def set_preferred_gender(message: Message, state: FSMContext) -> None:
        label = (message.text or "").strip()
        if label not in PREFERRED_GENDER_OPTIONS:
            await message.answer(
                "Выберите вариант кнопкой: «Любой», «Мужчина» или «Женщина».",
                reply_markup=PREFERRED_GENDER_KEYBOARD,
            )
            return

        await state.update_data(preferred_gender=PREFERRED_GENDER_OPTIONS[label])
        await state.set_state(SearchPreferences.min_age)
        await message.answer(
            "Укажите минимальный возраст собеседника (от 13 до 120 лет).",
            reply_markup=ReplyKeyboardRemove(),
        )

    @router.message(SearchPreferences.min_age)
    async def set_min_partner_age(message: Message, state: FSMContext) -> None:
        min_age = parse_age(message.text)
        if min_age is None:
            await message.answer(
                "Некорректный минимальный возраст. Введите целое число от 13 до 120."
            )
            return

        await state.update_data(min_partner_age=min_age)
        await state.set_state(SearchPreferences.max_age)
        await message.answer(
            f"Укажите максимальный возраст собеседника (от {min_age} до 120 лет)."
        )

    @router.message(SearchPreferences.max_age)
    async def set_max_partner_age(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        min_age = data.get("min_partner_age")
        max_age = parse_age(message.text)
        if max_age is None:
            await message.answer(
                "Некорректный максимальный возраст. Введите целое число от 13 до 120."
            )
            return

        preferred_gender = data.get("preferred_gender")
        if not isinstance(min_age, int):
            await state.clear()
            await message.answer(
                "Не удалось проверить диапазон. Отправьте /filters и попробуйте снова."
            )
            return
        if max_age < min_age:
            await message.answer(
                f"Максимальный возраст не может быть меньше минимального ({min_age}). "
                f"Введите значение от {min_age} до {MAX_AGE}."
            )
            return
        if preferred_gender not in {None, "male", "female"}:
            await state.clear()
            await message.answer(
                "Не удалось сохранить фильтры. Отправьте /filters и попробуйте снова."
            )
            return
        if message.from_user is None:
            return

        await repository.save_search_preferences(
            message.from_user.id, preferred_gender, min_age, max_age
        )
        await state.clear()
        gender_label = next(
            label
            for label, value in PREFERRED_GENDER_OPTIONS.items()
            if value == preferred_gender
        )
        await message.answer(
            f"Фильтры сохранены: пол — {gender_label.lower()}, "
            f"возраст — {min_age}–{max_age} лет. Теперь можно искать собеседника.",
            reply_markup=MAIN_KEYBOARD,
        )

    @router.message(Command("help"))
    @router.message(F.text == "ℹ️ Помощь")
    async def cmd_help(message: Message) -> None:
        await message.answer(
            "<b>Как пользоваться</b>\n"
            "Сначала укажите пол и возраст. Используйте «Начать поиск», чтобы найти "
            "собеседника. Во время поиска нажмите «Остановить поиск». В диалоге доступны "
            "«Искать дальше» и «Прекратить диалог». Для изменения данных используйте "
            "«Изменить свои данные» или /profile. Фильтры собеседника: /filters."
        )

    return router
