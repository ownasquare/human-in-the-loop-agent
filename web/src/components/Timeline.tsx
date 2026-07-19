import { Check, Circle, Clock3, ShieldAlert, TriangleAlert, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { RunStep } from "../types";
import { stepStatusLabel } from "../utils";
import { StatusBadge } from "./StatusBadge";

const STATUS_ICONS: Record<string, LucideIcon> = {
  succeeded: Check,
  completed: Check,
  approved: Check,
  rejected: X,
  failed: TriangleAlert,
  awaiting_approval: ShieldAlert,
  running: Clock3,
};

export function Timeline({ steps }: { steps: RunStep[] }) {
  if (!steps.length) return <p className="muted">Relay is preparing the plan.</p>;
  const ordered = [...steps].sort((left, right) => left.position - right.position);
  return (
    <ol className="timeline" aria-label="Workflow plan">
      {ordered.map((step) => {
        const Icon = STATUS_ICONS[step.status] ?? Circle;
        return (
          <li key={step.id} className={`timeline__item timeline__item--${step.status}`}>
            <span className="timeline__marker" aria-hidden="true"><Icon /></span>
            <div className="timeline__content">
              <div className="timeline__heading">
                <div>
                  <span className="timeline__number">Step {step.position + 1}</span>
                  <h3>{step.title || step.description}</h3>
                </div>
                <StatusBadge status={step.status} label={stepStatusLabel(step.status)} />
              </div>
              {step.title ? <p>{step.description}</p> : null}
              {step.result_summary ? <p className="timeline__result">{step.result_summary}</p> : null}
              {step.risk_level && step.risk_level !== "low" ? <small>Human review required</small> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
