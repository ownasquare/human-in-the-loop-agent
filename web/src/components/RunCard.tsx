import { ArrowUpRight, Clock3 } from "lucide-react";
import { Link } from "react-router-dom";
import type { RunRecord } from "../types";
import { formatRelative, runStatusLabel, titleFromInstruction } from "../utils";
import { StatusBadge } from "./StatusBadge";

export function RunCard({ run }: { run: RunRecord }) {
  const progress = run.total_steps && run.current_step !== null && run.current_step !== undefined
    ? Math.min(100, Math.round((run.current_step / run.total_steps) * 100))
    : null;
  return (
    <article className="run-card">
      <div className="run-card__header">
        <StatusBadge status={run.status} label={runStatusLabel(run.status)} />
        <span className="run-card__time">
          <Clock3 aria-hidden="true" />
          {formatRelative(run.updated_at ?? run.created_at)}
        </span>
      </div>
      <div>
        <h3>{run.title || titleFromInstruction(run.instruction)}</h3>
        <p>{run.summary || run.instruction}</p>
      </div>
      {progress !== null ? (
        <div className="run-card__progress">
          <div className="progress-track" aria-label={`${progress}% complete`}>
            <span style={{ width: `${progress}%` }} />
          </div>
          <small>{run.current_step} of {run.total_steps} steps</small>
        </div>
      ) : null}
      <Link className="text-link" to={`/runs/${run.id}`}>
        Open workflow
        <ArrowUpRight aria-hidden="true" />
      </Link>
    </article>
  );
}
