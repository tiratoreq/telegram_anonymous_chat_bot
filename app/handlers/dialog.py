import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from app.handlers.common import (
    DIALOG_KEYBOARD,
    MAIN_KEYBOARD,
    POST_DIALOG_KEYBOARD,
    SEARCHING_KEYBOARD,
)
from app.services.matcher import Matcher
from app.services.rate_limit import RateLimiter
from app.storage.repository import Repository

logger = logging.getLogger(__name__)


def create_dialog_router(
    matcher: Matcher,
    limiter: RateLimiter,
    repository: Repository,
    max_message_len: int,
) -> Router:
    router = Router(name="dialog")

    async def find_partner(message: Message) -> None:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        allowed, reason = await matcher.check_can_search(user_id)
        if not allowed:
            await message.answer(reason)
            return

        partner_id, already_searching = await matcher.find_partner(user_id)
        if already_searching:
            await message.answer(
                "Поиск уже идёт. Чтобы отменить его, нажмите «Остановить поиск».",
                reply_markup=SEARCHING_KEYBOARD,
            )
            return
        if partner_id is None:
            await message.answer(
                "Ищем собеседника. Чтобы остановить поиск, нажмите кнопку ниже.",
                reply_markup=SEARCHING_KEYBOARD,
            )
            return

        await message.answer(
            "✅ Собеседник найден! Можете начинать общаться.",
            reply_markup=DIALOG_KEYBOARD,
        )
        try:
            await message.bot.send_message(
                partner_id,
                "✅ Собеседник найден! Можете начинать общаться.",
                reply_markup=DIALOG_KEYBOARD,
            )
        except TelegramAPIError:
            logger.exception("Не удалось уведомить пользователя %s о найденной паре", partner_id)
            await repository.delete_pair(user_id, partner_id)
            await message.answer("Не удалось начать диалог с этим пользователем. Попробуйте /find ещё раз.")

    async def stop_dialog(message: Message, announce: bool = True) -> bool:
        if message.from_user is None:
            return False
        user_id = message.from_user.id
        partner_id = await repository.get_partner_id(user_id)
        if partner_id is None:
            if announce:
                await message.answer(
                    "❌ Вы не находитесь в активном диалоге.",
                    reply_markup=MAIN_KEYBOARD,
                )
            return False

        await repository.delete_pair(user_id, partner_id)
        if announce:
            await message.answer("Диалог завершён.", reply_markup=MAIN_KEYBOARD)
        try:
            await message.bot.send_message(
                partner_id,
                "Собеседник завершил диалог.",
                reply_markup=POST_DIALOG_KEYBOARD,
            )
        except TelegramAPIError:
            logger.exception("Не удалось уведомить пользователя %s о завершении диалога", partner_id)
        return True

    @router.message(Command("find"))
    @router.message(F.text == "Начать поиск")
    async def cmd_find(message: Message) -> None:
        await find_partner(message)

    @router.message(Command("cancel_search"))
    @router.message(F.text == "Остановить поиск")
    async def cmd_cancel_search(message: Message) -> None:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        if await repository.dequeue_user(user_id):
            await message.answer("Поиск остановлен.", reply_markup=MAIN_KEYBOARD)
            return
        if await repository.check_active_dialog(user_id):
            await message.answer(
                "Вы уже в диалоге. Используйте «Искать дальше» или «Прекратить диалог».",
                reply_markup=DIALOG_KEYBOARD,
            )
            return
        await message.answer("Сейчас поиск не выполняется.", reply_markup=MAIN_KEYBOARD)

    @router.message(Command("stop"))
    @router.message(F.text == "Прекратить диалог")
    async def cmd_stop(message: Message) -> None:
        await stop_dialog(message)

    @router.message(Command("next"))
    @router.message(F.text == "Искать дальше")
    async def cmd_next(message: Message) -> None:
        if message.from_user is None:
            return
        await stop_dialog(message, announce=False)
        await find_partner(message)

    @router.message(F.text == "Назад к меню")
    async def cmd_back(message: Message) -> None:
        await message.answer(
            "Вы вернулись в главное меню.",
            reply_markup=MAIN_KEYBOARD,
        )

    @router.message(F.text & ~F.text.startswith("/"))
    async def relay_text(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        user_id = message.from_user.id
        partner_id = await repository.get_partner_id(user_id)
        if partner_id is None:
            if not await repository.is_blocked(user_id):
                await message.answer("💬 Сначала найдите собеседника командой /find.")
            return
        if len(message.text) > max_message_len:
            await message.answer(f"Сообщение слишком длинное. Максимум: {max_message_len} символов.")
            return
        if not await limiter.allow(user_id):
            await message.answer("Слишком много сообщений. Подождите немного и попробуйте снова.")
            return

        try:
            await message.bot.copy_message(
                chat_id=partner_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except TelegramAPIError:
            logger.exception("Не удалось переслать сообщение от %s к %s", user_id, partner_id)
            await message.answer("Сообщение не удалось доставить. Диалог пока не завершён.")

    return router
