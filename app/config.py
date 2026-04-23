from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, field_validator
from typing import Any

class Settings(BaseSettings):
    # Настройки из .env подтянутся автоматически по именам полей
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    PGADMIN_DEFAULT_EMAIL: str
    PGADMIN_DEFAULT_PASSWORD: str
    # Собираем URL для SQLAlchemy (asyncpg для асинхронности)
    DATABASE_URL: str | None = None
    SECRET_KEY: str
    ALGORYTHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: str | None, info: Any) -> Any:
        if isinstance(v, str):
            return v
        return f"postgresql+asyncpg://{info.data['POSTGRES_USER']}:{info.data['POSTGRES_PASSWORD']}@localhost:5432/{info.data['POSTGRES_DB']}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

settings = Settings()
