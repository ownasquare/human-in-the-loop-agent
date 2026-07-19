from __future__ import annotations

import pytest

from relay.models import DecisionType
from relay.policy import ToolPolicy, UnknownToolError


def test_every_write_tool_requires_approval() -> None:
    policy = ToolPolicy()
    for tool_name in (
        "calendar_create",
        "email_send",
        "db_update_record",
        "purchase_order_create",
    ):
        assert policy.for_tool(tool_name).requires_approval is True


def test_record_and_purchase_actions_cannot_be_revised() -> None:
    policy = ToolPolicy()
    assert DecisionType.REVISE not in policy.for_tool("db_update_record").allowed_decisions
    assert DecisionType.REVISE not in policy.for_tool("purchase_order_create").allowed_decisions


def test_unknown_tool_is_fail_closed() -> None:
    with pytest.raises(UnknownToolError):
        ToolPolicy().for_tool("run_arbitrary_code")
