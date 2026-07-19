import { statusTone } from "../utils";

export function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`status-badge status-badge--${statusTone(status)}`}>{label}</span>;
}
