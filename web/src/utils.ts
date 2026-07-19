import type { StepStatus } from "./types";

const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

const EXACT_DATE_FORMATTER = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const TOOL_LABELS: Record<string, string> = {
  web_search: "Search the web",
  db_select: "Review customer records",
  calendar_availability: "Check calendar availability",
  calendar_create: "Create a calendar event",
  email_send: "Send an email",
  db_update_record: "Update a customer record",
  purchase_order_create: "Create a purchase order",
};

const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function formatDate(value?: string | null): string {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not available" : DATE_FORMATTER.format(date);
}

export function formatExactDate(value?: string | null): string {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not available" : EXACT_DATE_FORMATTER.format(date);
}

export function formatRelative(value?: string | null): string {
  if (!value) return "Recently";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently";
  const seconds = Math.round((date.getTime() - Date.now()) / 1_000);
  if (Math.abs(seconds) < 60) return RELATIVE_FORMATTER.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return RELATIVE_FORMATTER.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return RELATIVE_FORMATTER.format(hours, "hour");
  return RELATIVE_FORMATTER.format(Math.round(hours / 24), "day");
}

export function titleFromInstruction(instruction: string): string {
  const normalized = instruction.trim().replace(/\s+/g, " ");
  if (!normalized) return "Untitled workflow";
  return normalized.length > 68 ? `${normalized.slice(0, 65)}…` : normalized;
}

export function runStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "Queued",
    planning: "Planning",
    running: "Working",
    awaiting_approval: "Waiting for your decision",
    completed: "Complete",
    succeeded: "Complete",
    completed_with_rejections: "Complete with changes",
    needs_attention: "Needs review",
    failed: "Could not finish",
    cancelled: "Cancelled",
    outcome_unknown: "Needs review",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}

export function stepStatusLabel(status: StepStatus): string {
  return runStatusLabel(status);
}

export function statusTone(status: string): "neutral" | "info" | "success" | "warning" | "danger" {
  if (["completed", "succeeded", "approved", "ready"].includes(status)) return "success";
  if (["awaiting_approval", "pending", "demo"].includes(status)) return "warning";
  if (["failed", "rejected", "unavailable"].includes(status)) return "danger";
  if (["running", "planning", "queued"].includes(status)) return "info";
  return "neutral";
}

export function prettyKey(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function toolNameLabel(toolName: string): string {
  return TOOL_LABELS[toolName] ?? prettyKey(toolName || "workflow_action");
}

export function printableValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not set";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  if (typeof value === "string" || typeof value === "number" || typeof value === "bigint") {
    return String(value);
  }
  return "Not available";
}
