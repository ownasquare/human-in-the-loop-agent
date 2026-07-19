"""Secret-safe Relay configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_RESERVED_EMAIL_DOMAINS = {
    "localhost",
    "example",
    "invalid",
    "test",
    "example.com",
    "example.net",
    "example.org",
}


class LiveConnectorReadiness(TypedDict):
    """Credential-free configuration state for one live connector."""

    name: str
    configured: bool
    ready: bool
    detail: str


def _is_real_sender(value: str) -> bool:
    _, separator, domain = value.strip().lower().rpartition("@")
    if separator != "@" or not domain:
        return False
    return not any(
        domain == reserved or domain.endswith(f".{reserved}")
        for reserved in _RESERVED_EMAIL_DOMAINS
    )


class Settings(BaseSettings):
    """Runtime configuration with a credential-free deterministic default."""

    model_config = SettingsConfigDict(
        env_prefix="RELAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    mode: Literal["demo", "live"] = "demo"
    data_dir: Path = Path(".relay")
    static_dir: Path | None = None
    host: str = "127.0.0.1"
    allow_container_bind: bool = False
    port: int = Field(default=8000, ge=1, le=65535)
    action_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    max_plan_steps: int = Field(default=12, ge=1, le=25)
    max_search_results: int = Field(default=5, ge=1, le=10)
    request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        validation_alias=AliasChoices("RELAY_ANTHROPIC_MODEL", "ANTHROPIC_MODEL"),
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("RELAY_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    tavily_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("RELAY_TAVILY_API_KEY", "TAVILY_API_KEY"),
    )
    resend_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("RELAY_RESEND_API_KEY", "RESEND_API_KEY"),
    )
    email_from: str = "relay@example.invalid"
    acceptance_email_to: SecretStr | None = None
    google_calendar_access_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RELAY_GOOGLE_CALENDAR_ACCESS_TOKEN", "GOOGLE_CALENDAR_ACCESS_TOKEN"
        ),
    )
    google_calendar_id: str = Field(
        default="primary",
        validation_alias=AliasChoices("RELAY_GOOGLE_CALENDAR_ID", "GOOGLE_CALENDAR_ID"),
    )
    calendar_send_updates_default: Literal["none", "all", "externalOnly"] = "none"

    @field_validator(
        "anthropic_api_key",
        "tavily_api_key",
        "resend_api_key",
        "acceptance_email_to",
        "google_calendar_access_token",
        mode="before",
    )
    @classmethod
    def blank_secrets_are_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("host")
    @classmethod
    def require_token_for_public_bind(cls, value: str, info: object) -> str:
        # Cross-field enforcement is repeated by assert_safe_bind after construction.
        return value.strip()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "relay.sqlite3"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite3"

    @property
    def lock_dir(self) -> Path:
        return self.data_dir / "locks"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.data_dir.chmod(0o700)
            self.lock_dir.chmod(0o700)
        except OSError:
            pass

    def assert_safe_bind(self) -> None:
        if self.host == "0.0.0.0" and self.allow_container_bind:  # noqa: S104  # nosec B104
            return
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "Non-loopback binds are denied; containers require explicit "
                "RELAY_ALLOW_CONTAINER_BIND=true with host 0.0.0.0."
            )

    def connector_configuration(self) -> dict[str, bool]:
        return {
            "anthropic": self.anthropic_api_key is not None,
            "web_search": self.tavily_api_key is not None,
            "email": self.resend_api_key is not None and _is_real_sender(self.email_from),
            "calendar": self.google_calendar_access_token is not None,
            "database": True,
            "purchasing": True,
        }

    def live_connector_readiness(self) -> list[LiveConnectorReadiness]:
        """Report fixed, non-secret live configuration state without provider construction."""

        configured = self.connector_configuration()
        acceptance_target = self.acceptance_email_to
        resend_ready = configured["email"] and acceptance_target is not None
        if acceptance_target is not None:
            resend_ready = resend_ready and _is_real_sender(acceptance_target.get_secret_value())
        lane_configuration = {
            "claude": configured["anthropic"],
            "tavily": configured["web_search"],
            "resend": resend_ready,
            "google_calendar_read": configured["calendar"],
            "google_calendar_write": configured["calendar"],
            "database": configured["database"],
        }
        rows: list[LiveConnectorReadiness] = []
        for name, available in lane_configuration.items():
            rows.append(
                {
                    "name": name,
                    "configured": available,
                    "ready": available,
                    "detail": "configured" if available else "not_configured",
                }
            )
        rows.append(
            {
                "name": "purchasing",
                "configured": False,
                "ready": False,
                "detail": "disabled_in_live_mode",
            }
        )
        return rows


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    settings.assert_safe_bind()
    return settings


def clear_settings_cache() -> None:
    get_settings.cache_clear()
