from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay.models import (
    ApprovalDecisionRequest,
    DatabaseUpdateAction,
    DecisionType,
    EmailSendAction,
    action_payload_hash,
)


def test_payload_hash_is_canonical() -> None:
    first = EmailSendAction(to=["reviewer@example.com"], subject="Review", body="Please review")
    second = EmailSendAction.model_validate(
        {"body": "Please review", "subject": "Review", "to": ["reviewer@example.com"]}
    )
    assert action_payload_hash(first) == action_payload_hash(second)


def test_database_updates_are_structured_and_allowlisted() -> None:
    with pytest.raises(ValidationError, match="unsupported customer fields"):
        DatabaseUpdateAction(
            record_id="CUST-001",
            expected_version=1,
            changes={"password": "new-value"},
        )


def test_reject_requires_reason() -> None:
    with pytest.raises(ValidationError, match="rejection reason"):
        ApprovalDecisionRequest(
            decision=DecisionType.REJECT,
            expected_version=1,
            expected_payload_hash="a" * 64,
            idempotency_key="decision-key",
        )
