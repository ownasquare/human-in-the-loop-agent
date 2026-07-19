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


def test_reserved_email_senders_are_not_live_ready(tmp_path) -> None:
    for sender in (
        "relay@example.invalid",
        "relay@example.test",
        "relay@example.com",
        "relay@subdomain.example.net",
        "relay@localhost",
    ):
        settings = Settings(
            data_dir=tmp_path,
            resend_api_key=SecretStr("test-key"),
            email_from=sender,
        )
        assert settings.connector_configuration()["email"] is False


def test_real_sender_and_key_are_live_ready(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        resend_api_key=SecretStr("test-key"),
        email_from="relay@company.example.co",
    )

    assert settings.connector_configuration()["email"] is True


def test_live_connector_readiness_contains_only_fixed_non_secret_fields(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        anthropic_api_key=SecretStr("anthropic-fixture-secret"),
        tavily_api_key=SecretStr("tavily-fixture-secret"),
        resend_api_key=SecretStr("resend-fixture-secret"),
        email_from="relay@company.example.co",
        acceptance_email_to=SecretStr("controlled@company.example.co"),
        google_calendar_access_token=SecretStr("calendar-fixture-secret"),
    )

    report = settings.live_connector_readiness()

    assert [item["name"] for item in report] == [
        "claude",
        "tavily",
        "resend",
        "google_calendar_read",
        "google_calendar_write",
        "database",
        "purchasing",
    ]
    assert all(set(item) == {"name", "configured", "ready", "detail"} for item in report)
    assert report[-1] == {
        "name": "purchasing",
        "configured": False,
        "ready": False,
        "detail": "disabled_in_live_mode",
    }
    serialized = str(report)
    for secret in (
        "anthropic-fixture-secret",
        "tavily-fixture-secret",
        "resend-fixture-secret",
        "calendar-fixture-secret",
        "controlled@company.example.co",
    ):
        assert secret not in serialized
