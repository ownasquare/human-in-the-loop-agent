from __future__ import annotations

from pathlib import Path

import pytest

from relay.config import Settings
from relay.persistence import RelayRepository

_LIVE_LANES = ("claude", "tavily", "resend", "google-read", "google-write")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Allow separately acknowledged live-provider tests to run.",
    )
    parser.addoption(
        "--live-lane",
        choices=_LIVE_LANES,
        help="Select exactly one live-provider lane; all other live tests remain skipped.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_live = bool(config.getoption("--run-live"))
    selected_lane = config.getoption("--live-lane")
    for item in items:
        marker = item.get_closest_marker("live")
        if marker is None:
            continue
        item_lane = marker.kwargs.get("lane")
        if not run_live:
            reason = "live provider tests require --run-live"
        elif selected_lane is None:
            reason = "live provider tests require one explicit --live-lane"
        elif item_lane != selected_lane:
            reason = f"live provider test is outside selected lane {selected_lane}"
        else:
            continue
        item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(mode="demo", data_dir=tmp_path / "relay-data")


@pytest.fixture
def repository(settings: Settings) -> RelayRepository:
    settings.ensure_directories()
    return RelayRepository(settings.database_path)
