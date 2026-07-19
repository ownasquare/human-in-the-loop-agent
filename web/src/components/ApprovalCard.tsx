import { ArrowRight, Clock3, ShieldAlert } from "lucide-react";
import type { ApprovalProposal } from "../types";
import { formatRelative, prettyKey, printableValue } from "../utils";
import { StatusBadge } from "./StatusBadge";

interface ApprovalCardProps {
  approval: ApprovalProposal;
  onReview: (approval: ApprovalProposal) => void;
}

export function ApprovalCard({ approval, onReview }: ApprovalCardProps) {
  const previewEntries = Object.entries(approval.payload)
    .filter(([key]) => !["tool_name", "action_id"].includes(key))
    .slice(0, 3);
  return (
    <article className="approval-card">
      <header>
        <span className="approval-card__icon" aria-hidden="true"><ShieldAlert /></span>
        <div>
          <p className="eyebrow">Decision required</p>
          <h3>{approval.action_label || prettyKey(approval.tool_name)}</h3>
        </div>
        <StatusBadge status="awaiting_approval" label="Waiting" />
      </header>
      <p>{approval.description || approval.rationale || "Review the exact action before Relay continues."}</p>
      {approval.consequence ? (
        <div className="impact-note">
          <strong>What will happen</strong>
          <span>{approval.consequence}</span>
        </div>
      ) : null}
      {previewEntries.length ? (
        <dl className="approval-preview">
          {previewEntries.map(([key, value]) => (
            <div key={key}>
              <dt>{prettyKey(key)}</dt>
              <dd>{printableValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <footer>
        <span className="approval-card__time">
          <Clock3 aria-hidden="true" />
          {formatRelative(approval.created_at)}
        </span>
        <button className="button button--primary" type="button" onClick={() => onReview(approval)}>
          Review request
          <ArrowRight aria-hidden="true" />
        </button>
      </footer>
    </article>
  );
}
