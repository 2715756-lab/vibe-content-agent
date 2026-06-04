from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = 8088
    database_path: Path = Path("./data/agent.sqlite3")
    media_dir: Path = Path("./data/media")
    sources_path: Path = Path("./config/sources.yml")
    style_profile_path: Path = Path("./config/style_profile.md")
    style_profiles_dir: Path = Path("./config/styles")

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"

    daily_run_hour: int = 9
    daily_run_minute: int = 0
    author_name: str = "Артем"
    recommendation_keywords: str = (
        "AI,ИИ,LLM,агенты,разработка,vibecoding,вайбкодинг,стартап,OpenAI,Claude,Codex"
    )

    telegram_bot_token: str | None = None
    telegram_review_chat_id: str | None = None
    telegram_channel_id: str | None = None

    vk_access_token: str | None = None
    vk_owner_id: str | None = None

    admin_username: str = "admin"
    admin_password: str | None = None
    dzen_verification_token: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def keywords(self) -> list[str]:
        return [item.strip().lower() for item in self.recommendation_keywords.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
