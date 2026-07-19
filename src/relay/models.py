"""Validated domain and API models for Relay."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    computed_field,
    field_validator,
    model_validator,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Mode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    COMPLETED_WITH_REJECTIONS = "completed_with_rejections"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class DecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


class RiskLevel(StrEnum):
    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"


class AuditActor(StrEnum):
    OPERATOR = "operator"
    AGENT = "agent"
    SYSTEM = "system"
    TOOL = "tool"


class ReceiptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class WebSearchAction(StrictModel):
    tool_name: Literal["web_search"] = "web_search"
    query: str = Field(min_length=2, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)


class DatabaseSelectAction(StrictModel):
    tool_name: Literal["db_select"] = "db_select"
    query: str = Field(min_length=8, max_length=1000)


class CalendarAvailabilityAction(StrictModel):
    tool_name: Literal["calendar_availability"] = "calendar_availability"
    calendar_id: str = Field(default="primary", min_length=1, max_length=200)
    start_at: datetime
    end_at: datetime
    duration_minutes: int = Field(default=30, ge=15, le=480)

    @model_validator(mode="after")
    def valid_window(self) -> CalendarAvailabilityAction:
        if self.end_at <= self.start_at:
            raise ValueError("availability end_at must be after start_at")
        return self


class CalendarCreateAction(StrictModel):
    tool_name: Literal["calendar_create"] = "calendar_create"
    calendar_id: str = Field(default="primary", min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    start_at: datetime
    end_at: datetime
    attendees: list[str] = Field(default_factory=list, max_length=10)
    send_updates: Literal["none", "all", "externalOnly"] = "none"

    @field_validator("attendees")
    @classmethod
    def validate_attendees(cls, value: list[str]) -> list[str]:
        for item in value:
            if "@" not in item or len(item) > 320:
                raise ValueError("attendees must contain valid email-like addresses")
        return value

    @model_validator(mode="after")
    def valid_window(self) -> CalendarCreateAction:
        if self.end_at <= self.start_at:
            raise ValueError("calendar event end_at must be after start_at")
        return self


class EmailSendAction(StrictModel):
    tool_name: Literal["email_send"] = "email_send"
    sender: str = Field(default="relay@example.invalid", min_length=3, max_length=320)
    to: list[str] = Field(min_length=1, max_length=5)
    cc: list[str] = Field(default_factory=list, max_length=5)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("to", "cc")
    @classmethod
    def validate_recipients(cls, value: list[str]) -> list[str]:
        for item in value:
            if "@" not in item or len(item) > 320:
                raise ValueError("recipients must contain valid email-like addresses")
        return value

    @field_validator("sender")
    @classmethod
    def validate_sender(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("sender must be a valid email-like address")
        return value


ScalarValue: TypeAlias = str | int | float | bool | None


class DatabaseUpdateAction(StrictModel):
    tool_name: Literal["db_update_record"] = "db_update_record"
    table: Literal["customers"] = "customers"
    record_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)
    changes: dict[str, ScalarValue] = Field(min_length=1, max_length=8)

    @field_validator("changes")
    @classmethod
    def allowed_changes(cls, value: dict[str, ScalarValue]) -> dict[str, ScalarValue]:
        allowed = {"status", "next_action", "owner", "renewal_note"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unsupported customer fields: {', '.join(sorted(unknown))}")
        return value


class PurchaseOrderCreateAction(StrictModel):
    tool_name: Literal["purchase_order_create"] = "purchase_order_create"
    vendor: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, le=Decimal("10000.00"), decimal_places=2)
    currency: Literal["USD", "EUR", "GBP"] = "USD"
    description: str = Field(min_length=1, max_length=1000)


Action: TypeAlias = Annotated[
    WebSearchAction
    | DatabaseSelectAction
    | CalendarAvailabilityAction
    | CalendarCreateAction
    | EmailSendAction
    | DatabaseUpdateAction
    | PurchaseOrderCreateAction,
    Field(discriminator="tool_name"),
]
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(value: Action | dict[str, Any]) -> Action:
    if isinstance(value, BaseModel):
        return ACTION_ADAPTER.validate_python(value.model_dump(mode="json"))
    return ACTION_ADAPTER.validate_python(value)


def canonical_action_json(value: Action | dict[str, Any]) -> str:
    action = parse_action(value)
    return json.dumps(action.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def action_payload_hash(value: Action | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_action_json(value).encode("utf-8")).hexdigest()


class PlannedStep(StrictModel):
    id: str
    run_id: str
    position: int = Field(ge=0)
    action: Action
    status: StepStatus = StepStatus.PENDING
    receipt_id: str | None = None


class RunRecord(StrictModel):
    id: str
    thread_id: str
    instruction: str
    mode: Mode
    status: RunStatus
    current_index: int = 0
    summary: str = ""
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ActionProposal(StrictModel):
    id: str
    run_id: str
    step_id: str
    action: Action
    risk_level: RiskLevel
    title: str
    reason: str
    expected_effect: str
    payload_hash: str
    version: int = Field(ge=1)
    status: ProposalStatus
    interrupt_id: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tool_name(self) -> str:
        return self.action.tool_name


class ApprovalView(ActionProposal):
    allowed_decisions: list[DecisionType]
    review_context: dict[str, Any]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class ApprovalDecisionRequest(StrictModel):
    decision: DecisionType
    expected_version: int = Field(ge=1)
    expected_payload_hash: str = Field(min_length=64, max_length=64)
    reason: str = Field(default="", max_length=2000)
    revised_action: dict[str, Any] | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def decision_contract(self) -> ApprovalDecisionRequest:
        if self.decision is DecisionType.REJECT and not self.reason:
            raise ValueError("a rejection reason is required")
        if self.decision is DecisionType.REVISE and self.revised_action is None:
            raise ValueError("revised_action is required for a revision")
        if self.decision is not DecisionType.REVISE and self.revised_action is not None:
            raise ValueError("revised_action is only valid for a revision")
        return self


class ApprovalDecision(ApprovalDecisionRequest):
    id: str
    proposal_id: str
    created_at: datetime
    consumed_at: datetime | None = None


class ExecutionReceipt(StrictModel):
    id: str
    run_id: str
    proposal_id: str | None = None
    step_id: str
    tool_name: str
    status: ReceiptStatus
    provider: str
    provider_id: str | None = None
    idempotency_key: str
    request_hash: str
    response: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class AuditEvent(StrictModel):
    sequence: int
    event_id: str
    run_id: str
    proposal_id: str | None = None
    actor: AuditActor
    event_type: str
    detail: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


class RunCreateRequest(StrictModel):
    instruction: str = Field(min_length=3, max_length=5000)
    mode: Mode | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class ConnectorStatus(StrictModel):
    name: str
    mode: Mode
    configured: bool
    ready: bool
    detail: str


class RunDetail(StrictModel):
    run: RunRecord
    steps: list[PlannedStep]
    pending_approval: ApprovalView | None
    audit_events: list[AuditEvent]
    receipts: list[ExecutionReceipt]


class ListEnvelope(StrictModel):
    items: list[Any]
    total: int


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorResponse(StrictModel):
    error: ErrorDetail
