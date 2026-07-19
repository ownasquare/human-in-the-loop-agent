"""Deterministic, server-owned tool risk policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from relay.models import Action, DecisionType, RiskLevel, parse_action


class UnknownToolError(ValueError):
    """Raised when a model requests a tool Relay does not expose."""


@dataclass(frozen=True, slots=True)
class ToolRule:
    tool_name: str
    risk_level: RiskLevel
    requires_approval: bool
    allowed_decisions: frozenset[DecisionType]
    description: str


class ToolPolicy:
    """Immutable tool registry; model output cannot alter these decisions."""

    _rules: ClassVar[dict[str, ToolRule]] = {
        "web_search": ToolRule(
            "web_search", RiskLevel.LOW, False, frozenset(), "Search public sources"
        ),
        "db_select": ToolRule(
            "db_select", RiskLevel.LOW, False, frozenset(), "Read allowlisted records"
        ),
        "calendar_availability": ToolRule(
            "calendar_availability",
            RiskLevel.LOW,
            False,
            frozenset(),
            "Read calendar availability",
        ),
        "calendar_create": ToolRule(
            "calendar_create",
            RiskLevel.HIGH,
            True,
            frozenset({DecisionType.APPROVE, DecisionType.REJECT, DecisionType.REVISE}),
            "Create an external calendar event",
        ),
        "email_send": ToolRule(
            "email_send",
            RiskLevel.HIGH,
            True,
            frozenset({DecisionType.APPROVE, DecisionType.REJECT, DecisionType.REVISE}),
            "Send an external email",
        ),
        "db_update_record": ToolRule(
            "db_update_record",
            RiskLevel.HIGH,
            True,
            frozenset({DecisionType.APPROVE, DecisionType.REJECT}),
            "Update a durable customer record",
        ),
        "purchase_order_create": ToolRule(
            "purchase_order_create",
            RiskLevel.CRITICAL,
            True,
            frozenset({DecisionType.APPROVE, DecisionType.REJECT}),
            "Create a purchase order",
        ),
    }

    def for_tool(self, tool_name: str) -> ToolRule:
        try:
            return self._rules[tool_name]
        except KeyError as exc:
            raise UnknownToolError(f"Unknown tool denied: {tool_name}") from exc

    def for_action(self, action: Action | dict[str, object]) -> ToolRule:
        parsed = parse_action(action)
        return self.for_tool(parsed.tool_name)

    def capabilities(self) -> list[dict[str, object]]:
        return [
            {
                "tool_name": rule.tool_name,
                "risk_level": rule.risk_level.value,
                "requires_approval": rule.requires_approval,
                "allowed_decisions": sorted(item.value for item in rule.allowed_decisions),
                "description": rule.description,
            }
            for rule in self._rules.values()
        ]


DEFAULT_POLICY = ToolPolicy()
