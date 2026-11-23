from app.storage.repository import Repository


class QueueService:
    def __init__(self, repository: Repository):
        self.repository = repository

    async def try_enqueue(self, user_id: int) -> bool:
        if (
            await self.repository.check_user_in_queue(user_id)
            or await self.repository.check_active_dialog(user_id)
            or await self.repository.is_blocked(user_id)
        ):
            return False
        await self.repository.enqueue_user(user_id)
        return True

    async def try_dequeue(self, user_id: int) -> bool:
        if not await self.repository.check_user_in_queue(user_id):
            return False
        await self.repository.dequeue_user(user_id)
        return True

    async def try_match(self, user_id: int) -> tuple[int | None, bool]:
        return await self.repository.try_match_or_enqueue(user_id)
