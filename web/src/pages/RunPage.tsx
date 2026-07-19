import { ArrowLeft, CheckCircle2, FileCheck2, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApprovalDialog } from "../components/ApprovalDialog";
import { JsonDetails } from "../components/JsonDetails";
import { StatePanel, RetryButton } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";
import { Timeline } from "../components/Timeline";
import { useRun } from "../queries";
import { formatDate, prettyKey, runStatusLabel, titleFromInstruction, toolNameLabel } from "../utils";

const WRITE_TOOLS = new Set(["calendar_create", "email_send", "db_update_record", "purchase_order_create"]);

export function RunPage() {
  const { runId = "" } = useParams();
  const details = useRun(runId);
  const [approvalOpen, setApprovalOpen] = useState(false);

  if (details.isLoading) {
    return <StatePanel kind="loading" title="Opening workflow" description="Loading its plan, decisions, and receipts." />;
  }
  if (details.isError || !details.data) {
    return (
      <StatePanel
        kind="error"
        title="Workflow is unavailable"
        description="Relay could not load this workflow. It may have been reset or the local service may be unavailable."
        action={<RetryButton onClick={() => void details.refetch()} />}
      />
    );
  }

  const aggregate = details.data;
  const { run, steps, pending_approval: approval, receipts, audit_events: events } = aggregate;
  const title = run.title || titleFromInstruction(run.instruction);
  const latestWriteReceipt = [...receipts].reverse().find(
    (receipt) => WRITE_TOOLS.has(receipt.tool_name) && receipt.status === "succeeded",
  );

  return (
    <div className="page-stack run-page">
      <Link className="back-link" to="/"><ArrowLeft aria-hidden="true" /> Back to work queue</Link>
      <section className="run-hero">
        <div>
          <StatusBadge status={run.status} label={runStatusLabel(run.status)} />
          <h2>{title}</h2>
          {run.instruction.trim() !== title.trim() ? <p>{run.instruction}</p> : null}
          <div className="run-meta">
            <span>Started {formatDate(run.created_at)}</span>
            <span>{steps.length} planned steps</span>
            {run.mode ? <span>{prettyKey(run.mode)} mode</span> : null}
          </div>
        </div>
      </section>

      {approval && latestWriteReceipt ? (
        <section className="outcome-strip" aria-label="Latest confirmed result">
          <CheckCircle2 aria-hidden="true" />
          <div><strong>Last action confirmed</strong><span>{latestWriteReceipt.summary}</span></div>
        </section>
      ) : null}

      {approval ? (
        <section className="decision-banner" aria-labelledby="decision-banner-title">
          <span aria-hidden="true"><ShieldAlert /></span>
          <div>
            <p className="eyebrow">Waiting for you</p>
            <h2 id="decision-banner-title">{approval.action_label || prettyKey(approval.tool_name)}</h2>
            <p>{approval.consequence || approval.description || "Review the exact action to continue this workflow."}</p>
          </div>
          <button className="button button--primary" type="button" onClick={() => setApprovalOpen(true)}>Review request</button>
        </section>
      ) : null}

      {run.status === "needs_attention" ? (
        <section className="attention-banner" role="alert">
          <ShieldAlert aria-hidden="true" />
          <div><strong>Provider outcome needs review</strong><p>Do not retry this action until you check the receipts and audit history below.</p></div>
        </section>
      ) : null}

      {run.status === "failed" ? (
        <section className="attention-banner" role="alert">
          <ShieldAlert aria-hidden="true" />
          <div>
            <strong>Workflow stopped safely</strong>
            <p>{run.error_summary || "Relay stopped before it could safely complete this workflow."}</p>
            {run.error_code ? <small>Reason: {prettyKey(run.error_code)}</small> : null}
          </div>
        </section>
      ) : null}

      <div className="run-layout">
        <section className="surface-card" aria-labelledby="plan-title">
          <div className="section-heading"><div><p className="eyebrow">Progress</p><h2 id="plan-title">Plan and activity</h2></div></div>
          <Timeline steps={steps} />
        </section>
        <aside className="run-sidebar">
          <section className="surface-card" aria-labelledby="results-title">
            <div className="section-heading"><div><p className="eyebrow">Confirmed results</p><h2 id="results-title">What happened</h2></div></div>
            {receipts.length ? (
              <ul className="receipt-list">
                {receipts.map((receipt) => (
                  <li key={receipt.id}>
                    <span aria-hidden="true"><CheckCircle2 /></span>
                    <div>
                      <strong>{receipt.summary}</strong>
                      <small>{toolNameLabel(receipt.tool_name)} · {formatDate(receipt.created_at)}</small>
                      {receipt.error_code ? <small>Reason: {receipt.error_code.replaceAll("_", " ")}</small> : null}
                      {receipt.readback || receipt.provider || receipt.provider_reference ? (
                        <JsonDetails
                          label="Receipt details"
                          value={{
                            provider: receipt.provider,
                            provider_reference: receipt.provider_reference,
                            ...(receipt.readback ?? {}),
                          }}
                        />
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="compact-empty"><FileCheck2 aria-hidden="true" /><p>Receipts appear after an action is safely completed.</p></div>
            )}
          </section>
          <section className="surface-card" aria-labelledby="activity-title">
            <div className="section-heading"><div><p className="eyebrow">Recent</p><h2 id="activity-title">Activity</h2></div></div>
            {events.length ? (
              <ul className="compact-activity">
                {events.slice(-5).reverse().map((event) => (
                  <li key={event.id}><span>{event.summary}</span><small>{formatDate(event.occurred_at)}</small></li>
                ))}
              </ul>
            ) : <p className="muted">Activity will appear as Relay progresses.</p>}
          </section>
          <JsonDetails
            value={{ run_id: run.id, status: run.status, updated_at: run.updated_at, current_step: run.current_step }}
          />
        </aside>
      </div>
      <ApprovalDialog
        approval={approval}
        open={approvalOpen && approval !== null}
        onClose={() => setApprovalOpen(false)}
        onResolved={() => void details.refetch()}
      />
    </div>
  );
}
