from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///./config/localtune.db"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


settings = Settings()
