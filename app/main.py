import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import load_config
from app.handlers.admin import create_admin_router
from app.handlers.common import create_common_router
from app.handlers.dialog import create_dialog_router
from app.services.matcher import Matcher
from app.services.queue import QueueService
from app.services.rate_limit import RateLimiter
from app.storage.db import DatabaseManager
from app.storage.repository import Repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
PROXY_URL = "socks5://127.0.0.1:2080"
HTTP_REQUEST_TIMEOUT = 20
STARTUP_CONNECTION_ATTEMPTS = 3


async def verify_telegram_connection(bot: Bot) -> None:
    for attempt in range(1, STARTUP_CONNECTION_ATTEMPTS + 1):
        try:
            bot_info = await bot.get_me()
            logger.info("Подключено к Telegram как @%s", bot_info.username)
            return
        except TelegramNetworkError:
            if attempt == STARTUP_CONNECTION_ATTEMPTS:
                raise
            delay = 2 ** attempt
            logger.warning(
                "Telegram API не ответил (попытка %s/%s); повтор через %s сек.",
                attempt,
                STARTUP_CONNECTION_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)


async def main() -> None:
    settings = load_config()
    db_path = Path(settings.DB_PATH)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[1] / db_path

    database = DatabaseManager(str(db_path))
    session = AiohttpSession(proxy=PROXY_URL, timeout=HTTP_REQUEST_TIMEOUT)
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )

    try:
        await database.connect()
        repository = Repository(database)
        queue_service = QueueService(repository)
        matcher = Matcher(queue_service, repository)
        limiter = RateLimiter(settings.RATE_LIMIT_MAX, settings.RATE_LIMIT_WINDOW)

        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher.include_router(create_common_router(repository))
        dispatcher.include_router(
            create_dialog_router(matcher, limiter, repository, settings.MAX_MESSAGE_LEN)
        )
        dispatcher.include_router(
            create_admin_router(repository, settings.admin_ids_list)
        )

        logger.info("Бот запущен; база данных: %s", db_path)
        await verify_telegram_connection(bot)
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        try:
            await database.close_read()
        finally:
            try:
                await database.close_write()
            finally:
                if bot.session is not None:
                    await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Работа бота остановлена.")
    except TelegramNetworkError:
        logger.critical(
            "Не удалось подключиться к Telegram после нескольких попыток. "
            "Проверьте доступность прокси и ответ Telegram API."
        )
        raise SystemExit(1)
