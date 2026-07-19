import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { ApprovalDialog } from "../components/ApprovalDialog";
import { RetryButton, StatePanel } from "../components/StatePanel";
import { useApprovals } from "../queries";
import type { ApprovalProposal } from "../types";

export function ApprovalsPage() {
  const approvals = useApprovals();
  const [selected, setSelected] = useState<ApprovalProposal | null>(null);
  const items = approvals.data?.items ?? [];

  return (
    <div className="page-stack">
      <section className="page-intro">
        <p className="eyebrow">Your decisions</p>
        <h2>Approve only what you can verify.</h2>
        <p>
          Each request is locked to the details shown. If anything changes, Relay asks again.
        </p>
      </section>
      <section className="section-block" aria-labelledby="pending-approvals-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Pending</p>
            <h2 id="pending-approvals-title">Approval requests</h2>
          </div>
          {items.length ? <span className="count-label">{items.length} waiting</span> : null}
        </div>
        {approvals.isLoading ? (
          <StatePanel kind="loading" title="Loading approvals" description="Checking for decisions that need you." />
        ) : approvals.isError ? (
          <StatePanel
            kind="error"
            title="Approvals are unavailable"
            description="No decision was changed. Try loading the current queue again."
            action={<RetryButton onClick={() => void approvals.refetch()} />}
          />
        ) : items.length ? (
          <div className="approval-list">
            {items.map((approval) => (
              <ApprovalCard key={approval.id} approval={approval} onReview={setSelected} />
            ))}
          </div>
        ) : (
          <StatePanel
            kind="empty"
            icon={ShieldCheck}
            title="You are all caught up"
            description="New requests will appear here when a workflow reaches a write action."
          />
        )}
      </section>
      <ApprovalDialog
        approval={selected}
        open={selected !== null}
        onClose={() => setSelected(null)}
        onResolved={() => void approvals.refetch()}
      />
    </div>
  );
}
