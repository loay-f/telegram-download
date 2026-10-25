import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tglib.app.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        tg_api_id=0,
        tg_api_hash="",
        app_password="test-password",
        data_dir=tmp_path / "data",
        storage_dir=tmp_path / "storage",
        frontend_dir=tmp_path / "no-frontend",
    )
