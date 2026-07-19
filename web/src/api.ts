import type {
  ApprovalDecision,
  ApprovalProposal,
  AuditEvent,
  AuditFilters,
  Capabilities,
  ConnectorStatus,
  CreateRunInput,
  DecisionInput,
  ExecutionReceipt,
  ListResponse,
  RunAggregate,
  RunRecord,
  RunStatus,
  RunStep,
  StepStatus,
} from "./types";
import { toolNameLabel } from "./utils";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const REQUEST_TIMEOUT_MS = 15_000;

type JsonObject = Record<string, unknown>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(message: string, status = 0, code = "network_error", details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function scalarText(value: unknown, fallback = ""): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    return String(value);
  }
  return fallback;
}

function objectValue(value: unknown): JsonObject {
  return isObject(value) ? value : {};
}

function nullableObject(value: unknown): JsonObject | null {
  return isObject(value) ? value : null;
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function words(value: string): string {
  return value.replaceAll(".", " ").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stringList(value: unknown): string[] {
  return arrayValue(value).filter((item): item is string => typeof item === "string");
}

function actionDescription(action: JsonObject): string {
  const toolName = text(action.tool_name);
  if (toolName === "web_search") return `Search for “${text(action.query, "the requested information")}”`;
  if (toolName === "db_select") return "Read the approved customer records needed for this workflow.";
  if (toolName === "calendar_availability") {
    return `Check ${numberValue(action.duration_minutes, 30)}-minute availability on ${text(action.calendar_id, "the primary calendar")}.`;
  }
  if (toolName === "calendar_create") {
    const attendees = stringList(action.attendees);
    return `Create “${text(action.summary, "Calendar event") }”${attendees.length ? ` with ${attendees.join(", ")}` : ""}.`;
  }
  if (toolName === "email_send") {
    const recipients = stringList(action.to);
    const sender = text(action.sender);
    return `Send “${text(action.subject, "Prepared email") }”${sender ? ` from ${sender}` : ""}${recipients.length ? ` to ${recipients.join(", ")}` : ""}.`;
  }
  if (toolName === "db_update_record") {
    return `Update customer record ${text(action.record_id, "selected by the workflow")}.`;
  }
  if (toolName === "purchase_order_create") {
    const amount = scalarText(action.amount);
    const amountLabel = amount ? ` for ${text(action.currency, "USD")} ${amount}` : "";
    return `Prepare a purchase order with ${text(action.vendor, "the selected vendor")}${amountLabel}.`;
  }
  return toolNameLabel(toolName);
}

function auditSummary(eventType: string, detail: JsonObject): string {
  const toolName = text(detail.tool_name);
  const status = text(detail.status);
  const labels: Record<string, string> = {
    "run.created": "Workflow created.",
    "plan.created": `Plan prepared with ${numberValue(detail.step_count)} steps.`,
    "run.cancelled": "Workflow cancelled.",
    "approval.requested": `Approval requested for ${toolNameLabel(toolName).toLowerCase()}.`,
    "approval.approve": "Action approved by the operator.",
    "approval.reject": "Action rejected by the operator.",
    "approval.revise": "A revised action was submitted by the operator.",
    "tool.succeeded": `${toolNameLabel(toolName)} completed and read back.`,
    "tool.failed": `${toolNameLabel(toolName)} could not be completed.`,
    "tool.outcome_unknown": `${toolNameLabel(toolName)} has an unknown provider outcome.`,
  };
  if (eventType === "run.status_changed") {
    return status ? `Workflow status changed to ${words(status).toLowerCase()}.` : "Workflow status changed.";
  }
  return labels[eventType] ?? `${words(eventType)}.`;
}

function receiptSummary(toolName: string, status: string): string {
  const action = toolNameLabel(toolName);
  if (status === "succeeded") return `${action} completed`;
  if (status === "outcome_unknown") return `${action} needs provider verification`;
  if (status === "failed") return `${action} failed`;
  return `${action} · ${words(status).toLowerCase()}`;
}

function detailValue(value: unknown): unknown {
  if (!isObject(value)) return value;
  if ("detail" in value && isObject(value.detail)) return value.detail;
  if ("item" in value && isObject(value.item)) return value.item;
  return value;
}

function listValue(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (!isObject(value)) return [];
  if (Array.isArray(value.items)) return value.items;
  if (Array.isArray(value.results)) return value.results;
  if (isObject(value.detail) && Array.isArray(value.detail.items)) return value.detail.items;
  return [];
}

function normalizeRun(value: unknown): RunRecord {
  const item = objectValue(detailValue(value));
  return {
    id: text(item.id ?? item.run_id),
    instruction: text(item.instruction ?? item.request ?? item.goal),
    title: text(item.title ?? item.name) || null,
    status: text(item.status, "queued") as RunStatus,
    summary: text(item.summary ?? item.final_summary) || null,
    error_code: text(item.error_code) || null,
    error_summary: text(item.error_summary) || null,
    created_at: text(item.created_at ?? item.started_at, new Date(0).toISOString()),
    updated_at: text(item.updated_at) || null,
    completed_at: text(item.completed_at ?? item.finished_at) || null,
    current_step:
      typeof item.current_step === "number"
        ? item.current_step
        : typeof item.current_index === "number"
          ? item.current_index
          : null,
    total_steps: typeof item.total_steps === "number" ? item.total_steps : null,
    mode: text(item.mode) || null,
  };
}

function normalizeStep(value: unknown, index: number): RunStep {
  const item = objectValue(value);
  const action = objectValue(item.action);
  const toolName = text(item.tool_name ?? item.tool ?? action.tool_name);
  return {
    id: text(item.id ?? item.step_id, `step-${index + 1}`),
    run_id: text(item.run_id) || null,
    position: numberValue(item.position ?? item.index, index),
    title: text(item.title ?? item.label ?? action.title) || toolNameLabel(toolName),
    description: text(
      item.description ?? item.summary ?? action.description ?? action.title,
      actionDescription(action),
    ),
    tool_name: toolName || null,
    status: text(item.status, "pending") as StepStatus,
    risk_level: text(item.risk_level ?? item.risk) || null,
    result_summary: text(item.result_summary ?? item.result) || null,
  };
}

function normalizeApproval(value: unknown): ApprovalProposal | null {
  if (!isObject(value)) return null;
  const item = objectValue(detailValue(value));
  const id = text(item.id ?? item.proposal_id);
  if (!id) return null;
  const action = objectValue(item.action);
  return {
    id,
    run_id: text(item.run_id),
    step_id: text(item.step_id) || null,
    interrupt_id: text(item.interrupt_id) || null,
    version: numberValue(item.version ?? item.proposal_version ?? item.current_version, 1),
    payload_hash: text(item.payload_hash ?? item.action_hash),
    tool_name: text(item.tool_name ?? item.tool ?? action.tool_name, "unknown_action"),
    action_label: text(item.action_label ?? item.title ?? action.title) || null,
    description: text(item.description ?? item.summary ?? action.description) || null,
    rationale: text(item.rationale ?? item.reason ?? action.reason) || null,
    consequence:
      text(item.consequence ?? item.impact ?? item.expected_effect ?? action.expected_effect) || null,
    risk_level: text(item.risk_level ?? item.risk, "approval required"),
    status: text(item.status, "pending"),
    allowed_decisions: arrayValue(item.allowed_decisions ?? action.allowed_decisions).filter(
      (decision): decision is ApprovalDecision =>
        decision === "approve" || decision === "reject" || decision === "revise",
    ),
    payload: objectValue(item.payload ?? item.action_payload ?? item.arguments ?? item.action),
    review_context: nullableObject(item.review_context),
    before: nullableObject(item.before ?? item.before_state),
    after: nullableObject(item.after ?? item.after_state),
    created_at: text(item.created_at) || null,
    expires_at: text(item.expires_at) || null,
  };
}

function normalizeAuditEvent(value: unknown, index: number): AuditEvent {
  const item = objectValue(value);
  const detail = objectValue(item.details ?? item.metadata ?? item.detail);
  const eventType = text(item.event_type ?? item.type ?? item.action, "activity");
  const eventOutcome =
    eventType === "approval.requested"
      ? "pending"
      : eventType === "approval.approve"
        ? "approved"
        : eventType === "approval.reject"
          ? "rejected"
          : eventType === "approval.revise"
            ? "revised"
            : eventType.startsWith("tool.")
              ? eventType.slice("tool.".length)
              : "";
  return {
    id: text(item.id ?? item.event_id, `event-${index + 1}`),
    run_id: text(item.run_id) || null,
    event_type: eventType,
    outcome: text(item.outcome ?? item.status ?? detail.outcome ?? detail.status, eventOutcome) || null,
    summary: text(item.summary ?? item.message ?? item.description, auditSummary(eventType, detail)),
    tool_name: text(item.tool_name ?? item.tool ?? detail.tool_name) || null,
    actor: text(item.actor ?? item.actor_type) || null,
    occurred_at: text(item.occurred_at ?? item.created_at, new Date(0).toISOString()),
    hash: text(item.hash ?? item.event_hash) || null,
    previous_hash: text(item.previous_hash) || null,
    details: Object.keys(detail).length ? detail : null,
  };
}

function normalizeReceipt(value: unknown, index: number): ExecutionReceipt {
  const item = objectValue(value);
  const toolName = text(item.tool_name ?? item.tool, "action");
  const status = text(item.status, "completed");
  const providerId = text(item.provider_reference ?? item.external_id ?? item.provider_id);
  return {
    id: text(item.id ?? item.receipt_id, `receipt-${index + 1}`),
    run_id: text(item.run_id),
    proposal_id: text(item.proposal_id) || null,
    tool_name: toolName,
    status,
    provider: text(item.provider) || null,
    provider_reference: providerId || null,
    summary: text(item.summary ?? item.readback_summary, receiptSummary(toolName, status)),
    created_at: text(item.created_at ?? item.executed_at, new Date(0).toISOString()),
    readback: nullableObject(item.readback ?? item.response),
    error_code: text(item.error_code) || null,
  };
}

function normalizeAggregate(value: unknown): RunAggregate {
  const item = objectValue(detailValue(value));
  const runValue = item.run ?? item;
  const steps = arrayValue(item.steps ?? item.plan).map(normalizeStep);
  const run = normalizeRun(runValue);
  if (run.total_steps === null) run.total_steps = steps.length;
  return {
    run,
    steps,
    pending_approval: normalizeApproval(item.pending_approval ?? item.approval),
    audit_events: arrayValue(item.audit_events ?? item.activity).map(normalizeAuditEvent),
    receipts: arrayValue(item.receipts).map(normalizeReceipt),
  };
}

function normalizeConnector(value: unknown, index: number): ConnectorStatus {
  const item = objectValue(value);
  const name = text(item.name ?? item.label, "Connector");
  const identifier = text(item.id ?? item.connector_id) || name.toLowerCase().replaceAll(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || `connector-${index + 1}`;
  const ready = item.ready === true;
  const configured = item.configured === true;
  return {
    id: identifier,
    name: words(name),
    description: text(item.description) || null,
    category: text(item.category) || null,
    status: (text(item.status ?? item.readiness) || (ready ? "ready" : configured ? "unavailable" : "not_configured")) as ConnectorStatus["status"],
    mode: text(item.mode) || null,
    provider: text(item.provider) || null,
    last_checked_at: text(item.last_checked_at ?? item.checked_at) || null,
    detail: text(item.detail ?? item.message) || null,
    capabilities: arrayValue(item.capabilities).filter(
      (capability): capability is string => typeof capability === "string",
    ),
  };
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const body = await response.text();
  if (!body) return null;
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return body;
  }
}

function errorFromResponse(response: Response, payload: unknown): ApiError {
  const object = objectValue(payload);
  const error = isObject(object.error)
    ? object.error
    : isObject(object.detail)
      ? object.detail
      : object;
  const message = text(error.message ?? error.detail, `Request failed (${response.status})`);
  const code = text(error.code ?? error.type, `http_${response.status}`);
  return new ApiError(message, response.status, code, error.details ?? error);
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort("timeout"), REQUEST_TIMEOUT_MS);
  const abort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
      signal: controller.signal,
    });
    const payload = await parseBody(response);
    if (!response.ok) throw errorFromResponse(response, payload);
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) {
      const timedOut = !signal?.aborted;
      throw new ApiError(
        timedOut ? "Relay took too long to respond." : "Request cancelled.",
        0,
        timedOut ? "request_timeout" : "request_cancelled",
      );
    }
    throw new ApiError("Relay could not reach the local service.", 0, "network_error");
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export const relayApi = {
  async listRuns(signal?: AbortSignal): Promise<ListResponse<RunRecord>> {
    const payload = await request<unknown>("/runs", {}, signal);
    const object = objectValue(payload);
    return {
      items: listValue(payload).map(normalizeRun),
      total: typeof object.total === "number" ? object.total : undefined,
      next_cursor: text(object.next_cursor) || null,
    };
  },

  async createRun(input: CreateRunInput, signal?: AbortSignal): Promise<RunAggregate> {
    const payload = await request<unknown>(
      "/runs",
      { method: "POST", body: JSON.stringify(input) },
      signal,
    );
    return normalizeAggregate(payload);
  },

  async getRun(runId: string, signal?: AbortSignal): Promise<RunAggregate> {
    const payload = await request<unknown>(`/runs/${encodeURIComponent(runId)}`, {}, signal);
    return normalizeAggregate(payload);
  },

  async listApprovals(signal?: AbortSignal): Promise<ListResponse<ApprovalProposal>> {
    const payload = await request<unknown>("/approvals", {}, signal);
    const items = listValue(payload)
      .map(normalizeApproval)
      .filter((item): item is ApprovalProposal => item !== null);
    const object = objectValue(payload);
    return { items, total: typeof object.total === "number" ? object.total : undefined };
  },

  async getApproval(approvalId: string, signal?: AbortSignal): Promise<ApprovalProposal> {
    const payload = await request<unknown>(
      `/approvals/${encodeURIComponent(approvalId)}`,
      {},
      signal,
    );
    const approval = normalizeApproval(payload);
    if (!approval) throw new ApiError("Approval details were incomplete.", 502, "invalid_response");
    return approval;
  },

  async decide(
    approvalId: string,
    input: DecisionInput,
    signal?: AbortSignal,
  ): Promise<RunAggregate> {
    const payload = await request<unknown>(
      `/approvals/${encodeURIComponent(approvalId)}/decisions`,
      { method: "POST", body: JSON.stringify(input) },
      signal,
    );
    return normalizeAggregate(payload);
  },

  async listAuditEvents(
    filters: AuditFilters = {},
    signal?: AbortSignal,
  ): Promise<ListResponse<AuditEvent>> {
    const parameters = new URLSearchParams();
    if (filters.run_id) parameters.set("run_id", filters.run_id);
    if (filters.limit) parameters.set("limit", String(filters.limit));
    const query = parameters.size ? `?${parameters.toString()}` : "";
    const payload = await request<unknown>(`/audit-events${query}`, {}, signal);
    const object = objectValue(payload);
    const items = listValue(payload)
      .map(normalizeAuditEvent)
      .filter((item) => !filters.event_type || item.event_type.replaceAll(".", "_") === filters.event_type.replaceAll(".", "_"))
      .filter((item) => !filters.outcome || item.outcome === filters.outcome);
    return {
      items,
      total:
        filters.event_type || filters.outcome
          ? items.length
          : typeof object.total === "number"
            ? object.total
            : undefined,
    };
  },

  async listConnectors(signal?: AbortSignal): Promise<ListResponse<ConnectorStatus>> {
    const payload = await request<unknown>("/connectors", {}, signal);
    return { items: listValue(payload).map(normalizeConnector) };
  },

  async getCapabilities(signal?: AbortSignal): Promise<Capabilities> {
    const payload = await request<unknown>("/capabilities", {}, signal);
    return objectValue(detailValue(payload));
  },
};

export function toUserMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something unexpected happened. Try again.";
  if (error.code === "stale_approval") {
    return "This request changed while you were reviewing it. Reload the latest proposal.";
  }
  if (error.code === "outcome_unknown") {
    return "Relay could not confirm whether the provider accepted this action. Review the audit trail before retrying.";
  }
  return error.message;
}
