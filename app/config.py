from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: str = ""
    DB_PATH: str = "bot.db"
    MAX_MESSAGE_LEN: int = 4096
    RATE_LIMIT_MAX: int = 10
    RATE_LIMIT_WINDOW: int = 15

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def admin_ids_list(self) -> list[int]:
        try:
            return [int(value.strip()) for value in self.ADMIN_IDS.split(",") if value.strip()]
        except ValueError as exc:
            raise ValueError("ADMIN_IDS должен содержать ID через запятую") from exc


@lru_cache(maxsize=1)
def load_config() -> Settings:
    return Settings()