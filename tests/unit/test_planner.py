from __future__ import annotations

from relay.config import Settings
from relay.models import (
    CalendarAvailabilityAction,
    CalendarCreateAction,
    EmailSendAction,
)
from relay.planner import normalize_planned_actions


def test_consequential_connector_identity_is_normalized_before_review(tmp_path) -> None:
    settings = Settings(
        mode="live",
        data_dir=tmp_path,
        email_from="verified@example.com",
        google_calendar_id="configured-calendar@example.com",
    )
    actions = [
        EmailSendAction(
            sender="model-chosen@example.net",
            to=["reviewer@example.com"],
            subject="Review",
            body="Please review",
        ),
        CalendarAvailabilityAction(
            calendar_id="model-calendar@example.net",
            start_at="2030-01-15T09:00:00Z",
            end_at="2030-01-15T17:00:00Z",
        ),
        CalendarCreateAction(
            calendar_id="model-calendar@example.net",
            summary="Review",
            start_at="2030-01-15T10:00:00Z",
            end_at="2030-01-15T10:30:00Z",
        ),
    ]
    normalized = normalize_planned_actions(actions, settings)
    assert normalized[0].sender == "verified@example.com"
    assert normalized[1].calendar_id == "configured-calendar@example.com"
    assert normalized[2].calendar_id == "configured-calendar@example.com"
    assert actions[0].sender == "model-chosen@example.net"
