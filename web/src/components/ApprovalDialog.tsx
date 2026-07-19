import { ArrowRight, Check, PencilLine, ShieldCheck, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, toUserMessage } from "../api";
import { useDecision } from "../queries";
import type { ApprovalDecision, ApprovalProposal, DecisionInput } from "../types";
import { formatExactDate, prettyKey, printableValue } from "../utils";
import { Dialog } from "./Dialog";
import { JsonDetails } from "./JsonDetails";
import { useLiveRegion } from "./LiveRegion";

interface ApprovalDialogProps {
  approval: ApprovalProposal | null;
  open: boolean;
  onClose: () => void;
  onResolved?: () => void;
}

interface RevisionFields {
  sender: string;
  to: string;
  cc: string;
  subject: string;
  body: string;
  calendarId: string;
  summary: string;
  description: string;
  startAt: string;
  endAt: string;
  attendees: string;
  sendUpdates: "none" | "all" | "externalOnly";
}

const EMPTY_REVISION: RevisionFields = {
  sender: "",
  to: "",
  cc: "",
  subject: "",
  body: "",
  calendarId: "",
  summary: "",
  description: "",
  startAt: "",
  endAt: "",
  attendees: "",
  sendUpdates: "none",
};

const FIELD_LABELS: Record<string, string> = {
  calendar_id: "Calendar",
  start_at: "Starts",
  end_at: "Ends",
  send_updates: "Attendee notifications",
  body: "Message",
  record_id: "Record",
  expected_version: "Current record version",
};

const PRIMARY_REVIEW_CONTEXT = new Set(["notification_behavior"]);

function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? prettyKey(key);
}

function decisionValue(key: string, value: unknown): string {
  if ((key === "start_at" || key === "end_at") && typeof value === "string") {
    return formatExactDate(value);
  }
  if (key === "send_updates" && typeof value === "string") {
    return {
      none: "Do not notify attendees",
      all: "Notify all attendees",
      externalOnly: "Notify external attendees",
    }[value] ?? prettyKey(value);
  }
  if (key === "calendar_id" && value === "primary") return "Primary calendar";
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => printableValue(item)).join(", ") : "None";
  }
  return printableValue(value);
}

function changedFields(
  before: Record<string, unknown> | null | undefined,
  after: Record<string, unknown> | null | undefined,
): Array<{ key: string; before: unknown; after: unknown }> {
  if (!before && !after) return [];
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  const changes = [...keys]
    .filter((key) => key !== "version")
    .filter((key) => JSON.stringify(before?.[key]) !== JSON.stringify(after?.[key]))
    .map((key) => ({ key, before: before?.[key], after: after?.[key] }));
  return changes;
}

function stringField(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function listField(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").join(", ")
    : "";
}

function revisionFieldsFromJson(payloadJson: string): RevisionFields {
  if (!payloadJson) return EMPTY_REVISION;
  const parsed = JSON.parse(payloadJson) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return EMPTY_REVISION;
  const payload = parsed as Record<string, unknown>;
  const sendUpdates = stringField(payload, "send_updates");
  return {
    sender: stringField(payload, "sender"),
    to: listField(payload, "to"),
    cc: listField(payload, "cc"),
    subject: stringField(payload, "subject"),
    body: stringField(payload, "body"),
    calendarId: stringField(payload, "calendar_id"),
    summary: stringField(payload, "summary"),
    description: stringField(payload, "description"),
    startAt: stringField(payload, "start_at"),
    endAt: stringField(payload, "end_at"),
    attendees: listField(payload, "attendees"),
    sendUpdates:
      sendUpdates === "all" || sendUpdates === "externalOnly" ? sendUpdates : "none",
  };
}

function addresses(value: string): string[] {
  return value
    .split(/[\n,;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function validAddress(value: string): boolean {
  return /^[^\s@]+@[^\s@]+$/.test(value);
}

function hasOffset(value: string): boolean {
  return /(Z|[+-]\d{2}:\d{2})$/.test(value) && !Number.isNaN(Date.parse(value));
}

function newIdempotencyKey(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `relay-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ApprovalDialog({ approval, open, onClose, onResolved }: ApprovalDialogProps) {
  const rejectRef = useRef<HTMLButtonElement>(null);
  const reasonRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<ApprovalDecision | null>(null);
  const [reason, setReason] = useState("");
  const [revision, setRevision] = useState<RevisionFields>(EMPTY_REVISION);
  const [editError, setEditError] = useState("");
  const idempotencyKey = useMemo(
    () => `${approval?.id ?? "approval"}-${approval?.version ?? 0}-${newIdempotencyKey()}`,
    [approval?.id, approval?.version],
  );
  const mutation = useDecision(approval?.id ?? "", approval?.run_id ?? "");
  const resetMutation = mutation.reset;
  const { announce } = useLiveRegion();
  const approvalKey = approval ? `${approval.id}:${approval.version}` : "";
  const initialPayload = approval ? JSON.stringify(approval.payload, null, 2) : "";

  useEffect(() => {
    if (!open || !approvalKey) return;
    setMode(null);
    setReason("");
    setRevision(revisionFieldsFromJson(initialPayload));
    setEditError("");
    resetMutation();
  }, [approvalKey, initialPayload, open, resetMutation]);

  if (!approval) return null;
  const canRevise = approval.allowed_decisions?.includes("revise") ?? false;
  const revisionKind =
    approval.tool_name === "email_send"
      ? "email"
      : approval.tool_name === "calendar_create"
        ? "calendar"
        : null;

  const updateRevision = <Key extends keyof RevisionFields,>(key: Key, value: RevisionFields[Key]) => {
    setRevision((current) => ({ ...current, [key]: value }));
    setEditError("");
  };

  const buildRevisedAction = (): Record<string, unknown> | null => {
    if (revisionKind === "email") {
      const to = addresses(revision.to);
      const cc = addresses(revision.cc);
      if (!to.length || ![...to, ...cc].every(validAddress)) {
        setEditError("Add at least one valid To address and check each recipient.");
        return null;
      }
      if (!revision.subject.trim() || !revision.body.trim()) {
        setEditError("Subject and message body are required.");
        return null;
      }
      return {
        ...approval.payload,
        tool_name: "email_send",
        sender: revision.sender,
        to,
        cc,
        subject: revision.subject.trim(),
        body: revision.body,
      };
    }
    if (revisionKind === "calendar") {
      const attendees = addresses(revision.attendees);
      if (!attendees.every(validAddress)) {
        setEditError("Check each attendee address.");
        return null;
      }
      if (!revision.summary.trim() || !revision.description.trim()) {
        setEditError("Event title and description are required.");
        return null;
      }
      if (!hasOffset(revision.startAt) || !hasOffset(revision.endAt)) {
        setEditError("Start and end must be ISO 8601 times with Z or an explicit UTC offset.");
        return null;
      }
      if (Date.parse(revision.endAt) <= Date.parse(revision.startAt)) {
        setEditError("Event end must be after its start.");
        return null;
      }
      return {
        ...approval.payload,
        tool_name: "calendar_create",
        calendar_id: revision.calendarId,
        summary: revision.summary.trim(),
        description: revision.description.trim(),
        start_at: revision.startAt,
        end_at: revision.endAt,
        attendees,
        send_updates: revision.sendUpdates,
      };
    }
    setEditError("This action does not support operator revisions.");
    return null;
  };

  const submit = async (decision: ApprovalDecision) => {
    let revisedAction: Record<string, unknown> | undefined;
    if (decision === "revise") {
      revisedAction = buildRevisedAction() ?? undefined;
      if (!revisedAction) return;
      setEditError("");
    }
    const input: DecisionInput = {
      decision,
      expected_version: approval.version,
      expected_payload_hash: approval.payload_hash,
      idempotency_key: idempotencyKey,
      reason: reason.trim() || undefined,
      revised_action: revisedAction,
    };
    try {
      await mutation.mutateAsync(input);
      const label = decision === "approve" ? "approved" : decision === "reject" ? "rejected" : "revised";
      announce(`Action ${label}. Workflow state updated.`);
      onClose();
      onResolved?.();
    } catch (error) {
      if (error instanceof ApiError && error.code === "stale_approval") {
        announce("This request changed. Relay is loading the current approval.");
        onClose();
        onResolved?.();
        return;
      }
      announce("Decision was not saved. Review the error and try again.");
    }
  };

  const visiblePayload = Object.entries(approval.payload).filter(
    ([key]) => !["tool_name", "action_id"].includes(key),
  );
  const visibleReviewContext = Object.entries(approval.review_context ?? {}).filter(([key]) =>
    PRIMARY_REVIEW_CONTEXT.has(key),
  );
  const changes = changedFields(approval.before, approval.after);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={approval.action_label || prettyKey(approval.tool_name)}
      description="Review who or what will change. Nothing happens until you choose an action."
      size="large"
      initialFocusRef={rejectRef}
      footer={
        <>
          <button
            ref={rejectRef}
            className="button button--danger-quiet"
            type="button"
            disabled={mutation.isPending}
            onClick={() => {
              const enteringRejectMode = mode !== "reject";
              setMode(enteringRejectMode ? "reject" : null);
              if (enteringRejectMode) {
                window.requestAnimationFrame(() => reasonRef.current?.focus());
              }
            }}
          >
            <X aria-hidden="true" />
            {mode === "reject" ? "Cancel rejection" : "Reject"}
          </button>
          {canRevise ? (
            <button
              className="button button--secondary"
              type="button"
              disabled={mutation.isPending}
              onClick={() => {
                setMode(mode === "revise" ? null : "revise");
                setEditError("");
              }}
            >
              <PencilLine aria-hidden="true" />
              {mode === "revise" ? "Cancel edit" : "Edit"}
            </button>
          ) : null}
          {mode === "reject" ? (
            <button
              className="button button--danger"
              type="button"
              disabled={mutation.isPending || !reason.trim()}
              onClick={() => void submit("reject")}
            >
              <X aria-hidden="true" />
              {mutation.isPending ? "Saving…" : "Confirm rejection"}
            </button>
          ) : mode === "revise" ? (
            <button
              className="button button--primary"
              type="button"
              disabled={mutation.isPending || !revisionKind}
              onClick={() => void submit("revise")}
            >
              <Check aria-hidden="true" />
              Submit revision
            </button>
          ) : (
            <button
              className="button button--primary"
              type="button"
              disabled={mutation.isPending}
              onClick={() => void submit("approve")}
            >
              <ShieldCheck aria-hidden="true" />
              {mutation.isPending ? "Saving…" : "Approve action"}
            </button>
          )}
        </>
      }
    >
      <div className="decision-layout">
        <section className="decision-summary">
          <div className="risk-heading">
            <TriangleAlert aria-hidden="true" />
            <div>
              <span>{approval.risk_level === "critical" ? "High-impact action" : "Approval required"}</span>
              <strong>{approval.consequence || "This action writes to an external or durable system."}</strong>
            </div>
          </div>
          {approval.rationale ? <p>{approval.rationale}</p> : null}
        </section>

        {changes.length ? (
          <section aria-labelledby="change-preview-title">
            <h3 id="change-preview-title">Changes after approval</h3>
            <dl className="change-list">
              {changes.map((change) => (
                <div key={change.key}>
                  <dt>{fieldLabel(change.key)}</dt>
                  <dd>
                    <span>{decisionValue(change.key, change.before)}</span>
                    <ArrowRight aria-hidden="true" />
                    <strong>{decisionValue(change.key, change.after)}</strong>
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ) : (
          <section aria-labelledby="action-details-title">
            <h3 id="action-details-title">Action details</h3>
            <dl className="decision-fields">
              {visiblePayload.map(([key, value]) => (
                <div key={key}>
                  <dt>{fieldLabel(key)}</dt>
                  <dd>{decisionValue(key, value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {visibleReviewContext.length ? (
          <section aria-labelledby="review-context-title">
            <h3 id="review-context-title">What to expect</h3>
            <dl className="decision-fields">
              {visibleReviewContext.map(([key, value]) => (
                <div key={key}>
                  <dt>{fieldLabel(key)}</dt>
                  <dd>{decisionValue(key, value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {mode === "revise" ? (
          <section className="edit-panel" aria-labelledby="edit-action-title">
            <h3 id="edit-action-title">Edit the proposed action</h3>
            <p>Relay will validate your revision and present a new approval if the material action changes.</p>
            {revisionKind === "email" ? (
              <div className="revision-form">
                <label className="field field--span">
                  <span>Sender <small>(set by your workspace)</small></span>
                  <input value={revision.sender} readOnly />
                </label>
                <label className="field">
                  <span>To</span>
                  <input value={revision.to} onChange={(event) => updateRevision("to", event.target.value)} />
                </label>
                <label className="field">
                  <span>Cc <small>(optional)</small></span>
                  <input value={revision.cc} onChange={(event) => updateRevision("cc", event.target.value)} />
                </label>
                <label className="field field--span">
                  <span>Subject</span>
                  <input value={revision.subject} onChange={(event) => updateRevision("subject", event.target.value)} />
                </label>
                <label className="field field--span">
                  <span>Message body</span>
                  <textarea value={revision.body} onChange={(event) => updateRevision("body", event.target.value)} rows={8} />
                </label>
              </div>
            ) : revisionKind === "calendar" ? (
              <div className="revision-form">
                <label className="field field--span">
                  <span>Calendar <small>(set by your workspace)</small></span>
                  <input value={revision.calendarId} readOnly />
                </label>
                <label className="field field--span">
                  <span>Event title</span>
                  <input value={revision.summary} onChange={(event) => updateRevision("summary", event.target.value)} />
                </label>
                <label className="field field--span">
                  <span>Description</span>
                  <textarea value={revision.description} onChange={(event) => updateRevision("description", event.target.value)} rows={4} />
                </label>
                <label className="field">
                  <span>Starts at <small>(ISO 8601 with offset)</small></span>
                  <input value={revision.startAt} onChange={(event) => updateRevision("startAt", event.target.value)} />
                </label>
                <label className="field">
                  <span>Ends at <small>(ISO 8601 with offset)</small></span>
                  <input value={revision.endAt} onChange={(event) => updateRevision("endAt", event.target.value)} />
                </label>
                <label className="field">
                  <span>Attendees <small>(comma-separated)</small></span>
                  <input value={revision.attendees} onChange={(event) => updateRevision("attendees", event.target.value)} />
                </label>
                <label className="field">
                  <span>Attendee notifications</span>
                  <select
                    value={revision.sendUpdates}
                    onChange={(event) => updateRevision("sendUpdates", event.target.value as RevisionFields["sendUpdates"])}
                  >
                    <option value="none">Do not notify</option>
                    <option value="all">Notify everyone</option>
                    <option value="externalOnly">Notify external attendees</option>
                  </select>
                </label>
              </div>
            ) : (
              <p className="muted">This action cannot be revised. Approve it as shown or reject it.</p>
            )}
            {editError ? <div id="revision-error" className="inline-error" role="alert">{editError}</div> : null}
            <JsonDetails label="Original action JSON" value={approval.payload} />
          </section>
        ) : null}

        <label className="field field--compact">
          <span>
            {mode === "reject" ? (
              <>Rejection reason <small>(required)</small></>
            ) : (
              <>Decision note <small>(optional)</small></>
            )}
          </span>
          <input
            ref={reasonRef}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={500}
            required={mode === "reject"}
            placeholder={mode === "reject" ? "Explain why this action should not proceed" : "Add context for the audit trail"}
          />
        </label>

        <JsonDetails
          label="Technical binding details"
          value={{
            proposal_id: approval.id,
            run_id: approval.run_id,
            step_id: approval.step_id,
            version: approval.version,
            payload_hash: approval.payload_hash,
            interrupt_id: approval.interrupt_id,
            exact_action: approval.payload,
            review_context: approval.review_context,
            before: approval.before,
            after: approval.after,
          }}
        />
        {mutation.isError ? <div className="inline-error" role="alert">{toUserMessage(mutation.error)}</div> : null}
      </div>
    </Dialog>
  );
}
