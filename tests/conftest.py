from __future__ import annotations

from pathlib import Path

import pytest

from relay.config import Settings
from relay.persistence import RelayRepository


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(mode="demo", data_dir=tmp_path / "relay-data")


@pytest.fixture
def repository(settings: Settings) -> RelayRepository:
    settings.ensure_directories()
    return RelayRepository(settings.database_path)
