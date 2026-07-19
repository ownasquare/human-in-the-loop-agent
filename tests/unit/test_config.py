from __future__ import annotations

import pytest
from pydantic import SecretStr

from relay.config import Settings


def test_demo_is_credential_free_and_secret_repr_is_safe(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, anthropic_api_key=SecretStr("not-a-real-key"))
    assert settings.mode == "demo"
    assert "not-a-real-key" not in repr(settings)


def test_non_loopback_bind_is_denied_without_explicit_container_gate(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, host="192.0.2.10")
    with pytest.raises(ValueError, match="Non-loopback"):
        settings.assert_safe_bind()


def test_container_bind_requires_narrow_opt_in(tmp_path) -> None:
    denied = Settings(data_dir=tmp_path, host="0.0.0.0")  # noqa: S104 - safety test
    with pytest.raises(ValueError):
        denied.assert_safe_bind()
    Settings(
        data_dir=tmp_path,
        host="0.0.0.0",  # noqa: S104 - gated container-bind test
        allow_container_bind=True,
    ).assert_safe_bind()


def test_blank_secret_values_are_not_reported_as_configured(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        anthropic_api_key=" ",
        tavily_api_key="",
        resend_api_key=SecretStr("\t"),
        google_calendar_access_token="",
    )

    assert settings.anthropic_api_key is None
    assert settings.tavily_api_key is None
    assert settings.resend_api_key is None
    assert settings.google_calendar_access_token is None
    assert settings.connector_configuration() == {
        "anthropic": False,
        "web_search": False,
        "email": False,
        "calendar": False,
        "database": True,
        "purchasing": True,
    }
