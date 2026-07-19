import { CircleCheck, Clock3, Plus, ShieldAlert, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useApprovals, useRuns } from "../queries";
import { CreateRunDialog } from "../components/CreateRunDialog";
import { RetryButton, StatePanel } from "../components/StatePanel";
import { RunCard } from "../components/RunCard";

const ACTIVE = new Set(["queued", "planning", "running", "awaiting_approval"]);

export function WorkPage() {
  const [createOpen, setCreateOpen] = useState(false);
  const runs = useRuns();
  const approvals = useApprovals();
  const items = useMemo(() => runs.data?.items ?? [], [runs.data?.items]);
  const summary = useMemo(() => {
    let active = 0;
    let complete = 0;
    for (const run of items) {
      if (ACTIVE.has(run.status)) active += 1;
      if (run.status === "completed" || run.status === "completed_with_rejections") complete += 1;
    }
    return { active, complete, pending: approvals.data?.items.length ?? 0 };
  }, [approvals.data?.items.length, items]);

  return (
    <div className="page-stack">
      <section className="page-intro page-intro--action">
        <div>
          <p className="eyebrow">Your operations desk</p>
          <h2>Keep work moving without giving up control.</h2>
          <p>
            Relay handles safe research and preparation, then brings every message, calendar change,
            purchase, and record update back to you.
          </p>
        </div>
        <button className="button button--primary button--large" type="button" onClick={() => setCreateOpen(true)}>
          <Plus aria-hidden="true" />
          Start workflow
        </button>
      </section>

      {runs.isLoading ? (
        <section className="section-block" aria-label="Loading workflows">
          <div className="card-grid">
            {[0, 1, 2].map((item) => <div className="skeleton-card" key={item} aria-hidden="true" />)}
          </div>
        </section>
      ) : runs.isError ? (
        <StatePanel
          kind={navigator.onLine ? "error" : "offline"}
          title={navigator.onLine ? "Workflows are unavailable" : "You are offline"}
          description={navigator.onLine ? "Relay could not load the work queue." : "Reconnect to refresh current workflow state."}
          action={<RetryButton onClick={() => void runs.refetch()} />}
        />
      ) : items.length ? (
        <>
          <section className="summary-grid" aria-label="Workspace summary">
            <article className="summary-card">
              <span className="summary-card__icon summary-card__icon--blue" aria-hidden="true"><Clock3 /></span>
              <div><strong>{summary.active}</strong><span>In progress</span></div>
            </article>
            <article className="summary-card">
              <span className="summary-card__icon summary-card__icon--amber" aria-hidden="true"><ShieldAlert /></span>
              <div><strong>{summary.pending}</strong><span>Need a decision</span></div>
            </article>
            <article className="summary-card">
              <span className="summary-card__icon summary-card__icon--green" aria-hidden="true"><CircleCheck /></span>
              <div><strong>{summary.complete}</strong><span>Completed</span></div>
            </article>
          </section>
          <section className="section-block" aria-labelledby="recent-workflows-title">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Recent work</p>
                <h2 id="recent-workflows-title">Workflows</h2>
              </div>
              <button className="button button--secondary" type="button" onClick={() => setCreateOpen(true)}>
                <Plus aria-hidden="true" />
                New
              </button>
            </div>
            <div className="card-grid">
              {items.map((run) => <RunCard key={run.id} run={run} />)}
            </div>
          </section>
        </>
      ) : (
        <section className="first-run-guide" aria-labelledby="first-run-title">
          <header>
            <span className="first-run-guide__icon" aria-hidden="true"><Sparkles /></span>
            <div>
              <p className="eyebrow">Guided demo</p>
              <h2 id="first-run-title">Start, review, confirm.</h2>
              <p>Relay completes safe preparation, pauses for your decision, and shows what happened next.</p>
            </div>
          </header>
          <ol className="core-flow">
            <li><span>1</span><div><strong>Start</strong><p>Choose one outcome for Relay to prepare.</p></div></li>
            <li><span>2</span><div><strong>Review</strong><p>Check the exact message, event, record change, or purchase.</p></div></li>
            <li><span>3</span><div><strong>Confirm</strong><p>Approve or reject, then verify the recorded result.</p></div></li>
          </ol>
        </section>
      )}
      <CreateRunDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
