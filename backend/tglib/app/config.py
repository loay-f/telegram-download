from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level configuration from the environment / .env file.

    User-tunable options (extensions, worker count, ...) live in the DB
    `settings` table instead, so they can be changed from the UI.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tg_api_id: int = 0
    tg_api_hash: str = ""
    app_password: str = ""
    data_dir: Path = Path("/data")
    storage_dir: Path = Path("/storage")
    port: int = 8000
    log_level: str = "INFO"
    # Turn on when the app is served over HTTPS (reverse proxy); adds the
    # Secure flag to the login cookie.
    cookie_secure: bool = False
    frontend_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )

    @field_validator("tg_api_id", "port", mode="before")
    @classmethod
    def _blank_int(cls, v, info):
        # `TG_API_ID=` in .env means "not configured yet", not an error.
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def session_path(self) -> Path:
        # Telethon appends ".session" itself.
        return self.data_dir / "telegram"

    @property
    def tmp_dir(self) -> Path:
        return self.storage_dir / "tmp"

    @property
    def library_dir(self) -> Path:
        return self.storage_dir / "library"

    @property
    def telegram_configured(self) -> bool:
        return bool(self.tg_api_id and self.tg_api_hash)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.storage_dir, self.tmp_dir, self.library_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
