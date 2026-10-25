from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    # Keep the app's own logging configuration.
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade_to_head(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")
