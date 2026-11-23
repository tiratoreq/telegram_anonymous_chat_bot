from app.services.queue import QueueService
from app.storage.repository import Repository


class Matcher:
    def __init__(self, queue_service: QueueService, repository: Repository):
        self.queue_service = queue_service
        self.repository = repository

    async def find_partner(self, user_id: int) -> tuple[int | None, bool]:
        return await self.queue_service.try_match(user_id)

    async def check_can_search(self, user_id: int) -> tuple[bool, str]:
        profile = await self.repository.get_profile(user_id)
        if not profile or profile["gender"] is None or profile["age"] is None:
            return False, "Сначала заполните профиль: отправьте /start и укажите пол и возраст."
        if await self.repository.is_blocked(user_id):
            return False, "Вы не можете искать собеседника."
        if await self.repository.check_active_dialog(user_id):
            return False, "У вас уже есть активный диалог."
        return True, "Поиск разрешен."
