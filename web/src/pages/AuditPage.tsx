import { Download, Filter, Link2, ScrollText } from "lucide-react";
import { useMemo, useState } from "react";
import { JsonDetails } from "../components/JsonDetails";
import { RetryButton, StatePanel } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";
import { useAuditEvents } from "../queries";
import type { AuditFilters } from "../types";
import { formatDate, prettyKey } from "../utils";
import { InfoTooltip } from "../components/InfoTooltip";

export function AuditPage() {
  const [eventType, setEventType] = useState("");
  const [outcome, setOutcome] = useState("");
  const filters = useMemo<AuditFilters>(
    () => ({ event_type: eventType || undefined, outcome: outcome || undefined, limit: 100 }),
    [eventType, outcome],
  );
  const audit = useAuditEvents(filters);
  const items = audit.data?.items ?? [];

  const exportVisible = () => {
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `relay-audit-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="page-stack">
      <section className="page-intro page-intro--action">
        <div>
          <p className="eyebrow">Decision and action history</p>
          <div className="heading-with-help">
            <h2>See every decision and result.</h2>
            <InfoTooltip label="About verified history">
              Entries are linked so local changes can be detected. Sensitive connector values stay out of this view.
            </InfoTooltip>
          </div>
          <p>Filter the record or export the events currently shown.</p>
        </div>
        <button className="button button--secondary" type="button" onClick={exportVisible} disabled={!items.length}>
          <Download aria-hidden="true" />
          Export visible events
        </button>
      </section>
      <section className="filter-bar" aria-label="Audit filters">
        <span className="filter-bar__label"><Filter aria-hidden="true" /> Filters</span>
        <label>
          <span>Event type</span>
          <select value={eventType} onChange={(event) => setEventType(event.target.value)}>
            <option value="">All events</option>
            <option value="run.created">Run created</option>
            <option value="plan.created">Plan created</option>
            <option value="approval.requested">Approval requested</option>
            <option value="approval.approve">Action approved</option>
            <option value="approval.reject">Action rejected</option>
            <option value="tool.succeeded">Tool succeeded</option>
            <option value="tool.failed">Tool failed</option>
            <option value="run.status_changed">Run status changed</option>
          </select>
        </label>
        <label>
          <span>Outcome</span>
          <select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
            <option value="">Every outcome</option>
            <option value="succeeded">Succeeded</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="failed">Failed</option>
            <option value="outcome_unknown">Unknown outcome</option>
          </select>
        </label>
      </section>
      <section className="section-block" aria-labelledby="audit-events-title">
        <div className="section-heading">
          <div><p className="eyebrow">History</p><h2 id="audit-events-title">Audit events</h2></div>
          {audit.data?.total !== undefined ? <span className="count-label">{audit.data.total} events</span> : null}
        </div>
        {audit.isLoading ? (
          <StatePanel kind="loading" title="Loading the audit trail" description="Verifying the latest event sequence." />
        ) : audit.isError ? (
          <StatePanel kind="error" title="Audit history is unavailable" description="Try loading the read-only history again." action={<RetryButton onClick={() => void audit.refetch()} />} />
        ) : items.length ? (
          <ol className="audit-list">
            {items.map((event) => (
              <li key={event.id} className="audit-event">
                <span className="audit-event__line" aria-hidden="true"><ScrollText /></span>
                <article>
                  <header>
                    <div>
                      <span className="audit-event__type">{prettyKey(event.event_type)}</span>
                      <h3>{event.summary}</h3>
                    </div>
                    {event.outcome ? <StatusBadge status={event.outcome} label={prettyKey(event.outcome)} /> : null}
                  </header>
                  <div className="audit-event__meta">
                    <span>{formatDate(event.occurred_at)}</span>
                    {event.actor ? <span>Actor: {prettyKey(event.actor)}</span> : null}
                    {event.tool_name ? <span>Action: {prettyKey(event.tool_name)}</span> : null}
                    {event.run_id ? <span><Link2 aria-hidden="true" /> Run {event.run_id.slice(0, 8)}</span> : null}
                  </div>
                  {event.details ? <JsonDetails value={event.details} /> : null}
                  {event.hash ? <JsonDetails label="Integrity details" value={{ event_hash: event.hash, previous_hash: event.previous_hash }} /> : null}
                </article>
              </li>
            ))}
          </ol>
        ) : (
          <StatePanel kind="empty" title="No matching events" description="Adjust the filters or start a workflow to create the first audit entries." />
        )}
      </section>
    </div>
  );
}
