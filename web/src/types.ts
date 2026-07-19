export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "completed_with_rejections"
  | "needs_attention"
  | "failed"
  | "cancelled";

export type StepStatus =
  | "pending"
  | "running"
  | "awaiting_approval"
  | "rejected"
  | "succeeded"
  | "failed";

export type ApprovalDecision = "approve" | "reject" | "revise";
export type ThemePreference = "system" | "light" | "dark";

export interface RunRecord {
  id: string;
  instruction: string;
  title?: string | null;
  status: RunStatus;
  summary?: string | null;
  error_code?: string | null;
  error_summary?: string | null;
  created_at: string;
  updated_at?: string | null;
  completed_at?: string | null;
  current_step?: number | null;
  total_steps?: number | null;
  mode?: string | null;
}

export interface RunStep {
  id: string;
  run_id?: string | null;
  position: number;
  title?: string | null;
  description: string;
  tool_name?: string | null;
  status: StepStatus;
  risk_level?: string | null;
  result_summary?: string | null;
}

export interface ApprovalProposal {
  id: string;
  run_id: string;
  step_id?: string | null;
  interrupt_id?: string | null;
  version: number;
  payload_hash: string;
  tool_name: string;
  action_label?: string | null;
  description?: string | null;
  rationale?: string | null;
  consequence?: string | null;
  risk_level?: string | null;
  status: string;
  allowed_decisions?: ApprovalDecision[];
  payload: Record<string, unknown>;
  review_context?: Record<string, unknown> | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  created_at?: string | null;
  expires_at?: string | null;
}

export interface AuditEvent {
  id: string;
  run_id?: string | null;
  event_type: string;
  outcome?: string | null;
  summary: string;
  tool_name?: string | null;
  actor?: string | null;
  occurred_at: string;
  hash?: string | null;
  previous_hash?: string | null;
  details?: Record<string, unknown> | null;
}

export interface ExecutionReceipt {
  id: string;
  run_id: string;
  proposal_id?: string | null;
  tool_name: string;
  status: string;
  provider?: string | null;
  provider_reference?: string | null;
  summary: string;
  created_at: string;
  readback?: Record<string, unknown> | null;
  error_code?: string | null;
}

export interface RunAggregate {
  run: RunRecord;
  steps: RunStep[];
  pending_approval: ApprovalProposal | null;
  audit_events: AuditEvent[];
  receipts: ExecutionReceipt[];
}

export interface ConnectorStatus {
  id: string;
  name: string;
  description?: string | null;
  category?: string | null;
  status: "ready" | "demo" | "not_configured" | "unavailable";
  mode?: string | null;
  provider?: string | null;
  last_checked_at?: string | null;
  detail?: string | null;
  capabilities?: string[];
}

export interface Capabilities {
  application?: string;
  version?: string;
  mode?: "demo" | "live";
  demo_mode?: boolean;
  write_tools_require_approval?: boolean;
  supports_edits?: boolean;
  model_configured?: boolean;
}

export interface ListResponse<T> {
  items: T[];
  total?: number;
  next_cursor?: string | null;
}

export interface CreateRunInput {
  instruction: string;
  mode?: "demo" | "live";
  idempotency_key?: string;
}

export interface DecisionInput {
  decision: ApprovalDecision;
  expected_version: number;
  expected_payload_hash: string;
  idempotency_key: string;
  reason?: string;
  revised_action?: Record<string, unknown>;
}

export interface AuditFilters {
  run_id?: string;
  event_type?: string;
  outcome?: string;
  limit?: number;
}
