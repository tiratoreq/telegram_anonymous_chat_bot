import logging

from aiogram import Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from app.storage.repository import Repository

logger = logging.getLogger(__name__)


class AdminFilter(BaseFilter):
    def __init__(self, admin_ids: set[int]):
        self.admin_ids = admin_ids

    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in self.admin_ids


def create_admin_router(repository: Repository, admin_ids: list[int]) -> Router:
    router = Router(name="admin")
    is_admin = AdminFilter(set(admin_ids))

    @router.message(Command("block"), is_admin)
    async def cmd_block(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await message.answer("Использование: /block <user_id> [причина]")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("ID пользователя должен быть целым числом.")
            return

        if message.from_user is None:
            return
        reason = parts[2] if len(parts) == 3 else ""
        await repository.block_user(target_id, reason)
        await message.answer(f"Пользователь {target_id} заблокирован.")

    @router.message(Command("unblock"), is_admin)
    async def cmd_unblock(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Использование: /unblock <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("ID пользователя должен быть целым числом.")
            return

        await repository.unblock_user(target_id)
        await message.answer(f"Пользователь {target_id} разблокирован.")

    @router.message(Command("stats"), is_admin)
    async def cmd_stats(message: Message) -> None:
        stats = await repository.get_system_stats()
        await message.answer(
            "<b>Статистика чат-бота</b>\n"
            f"👥 Пользователей: {stats['total_users']}\n"
            f"⏳ В очереди: {stats['queue_size']}\n"
            f"🔗 Активных пар: {stats['active_pairs']}\n"
            f"🚫 Заблокированных: {stats['blocked_users']}"
        )

    @router.message(Command("block", "unblock", "stats"))
    async def reject_non_admin(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else "unknown"
        logger.warning("Пользователь %s попытался выполнить административную команду", user_id)
        await message.answer("Эта команда доступна только администраторам.")

    return router
