import { ArrowRight, CalendarCheck, FileCheck2, Plus, ShoppingCart } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toUserMessage } from "../api";
import { useCapabilities, useCreateRun } from "../queries";
import { useLiveRegion } from "./LiveRegion";
import { Dialog } from "./Dialog";

const TEMPLATES = [
  {
    id: "renewal_outreach",
    title: "Prepare renewal outreach",
    description: "Research an account, find a time, schedule a review, email the customer, and update the record.",
    instruction:
      "Prepare renewal outreach for Northstar Labs. Review the account, find a suitable meeting time, schedule the review, send the customer email, and update the customer record.",
    icon: CalendarCheck,
  },
  {
    id: "purchase_request",
    title: "Prepare a purchase request",
    description: "Research a vendor, prepare a purchase order, and route every commitment for approval.",
    instruction:
      "Compare the approved office chair vendors, recommend the best fit, and prepare a purchase order for review without committing funds until approved.",
    icon: ShoppingCart,
  },
  {
    id: "record_follow_up",
    title: "Follow up on an account",
    description: "Review a customer record, draft the next steps, and pause before any update or message.",
    instruction:
      "Review the Acme account, identify the next best action, draft the follow-up, and ask before sending or changing the customer record.",
    icon: FileCheck2,
  },
] as const;

const DEMO_INSTRUCTION = "Prepare the Acme renewal workflow";

interface CreateRunDialogProps {
  open: boolean;
  onClose: () => void;
}

export function CreateRunDialog({ open, onClose }: CreateRunDialogProps) {
  const [instruction, setInstruction] = useState<string>(TEMPLATES[0].instruction);
  const [templateId, setTemplateId] = useState<string>(TEMPLATES[0].id);
  const submitRef = useRef<HTMLButtonElement>(null);
  const mutation = useCreateRun();
  const capabilities = useCapabilities();
  const serverMode = capabilities.data?.mode;
  const modeKnown = serverMode === "demo" || serverMode === "live";
  const demoMode = serverMode === "demo";
  const resetMutation = mutation.reset;
  const navigate = useNavigate();
  const { announce } = useLiveRegion();

  useEffect(() => {
    if (open) resetMutation();
  }, [open, resetMutation]);

  const submit = async () => {
    if (!serverMode) {
      announce("Relay has not confirmed this workspace mode. No workflow was started.");
      return;
    }
    const normalized = demoMode ? DEMO_INSTRUCTION : instruction.trim();
    if (!normalized) return;
    try {
      const aggregate = await mutation.mutateAsync({ instruction: normalized, mode: serverMode });
      announce("Workflow created. Relay is ready for the next step.");
      onClose();
      void navigate(`/runs/${aggregate.run.id}`);
    } catch {
      announce("Workflow could not be created.");
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={demoMode ? "Start the demo walkthrough" : "Start a workflow"}
      description={
        !modeKnown
          ? "Relay must confirm this workspace's operating mode before it can start anything."
          : demoMode
            ? "Run one fixed Acme renewal scenario with three safe reads and four human decisions."
            : "Describe the outcome. Relay will complete safe steps and pause before every write action."
      }
      size="large"
      initialFocusRef={submitRef}
      footer={
        <>
          <button className="button button--secondary" type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            ref={submitRef}
            className="button button--primary"
            type="button"
            disabled={!modeKnown || (!demoMode && !instruction.trim()) || mutation.isPending}
            onClick={() => void submit()}
          >
            {mutation.isPending
              ? "Starting…"
              : !modeKnown
                ? "Waiting for workspace"
                : demoMode
                  ? "Start walkthrough"
                  : "Start workflow"}
            {!mutation.isPending ? <ArrowRight aria-hidden="true" /> : null}
          </button>
        </>
      }
    >
      <form
        className="create-workflow"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {!modeKnown ? (
          <section
            className="demo-walkthrough-card"
            role={capabilities.isError ? "alert" : "status"}
          >
            <CalendarCheck aria-hidden="true" />
            <div>
              <p className="eyebrow">Safety check</p>
              <h3>
                {capabilities.isError
                  ? "Workspace mode is unavailable"
                  : "Confirming workspace mode"}
              </h3>
              <p>
                {capabilities.isError
                  ? "Relay will not start a workflow until it can confirm whether this workspace is demo or live."
                  : "No workflow can start while this check is in progress."}
              </p>
              {capabilities.isError ? (
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => void capabilities.refetch()}
                >
                  Try again
                </button>
              ) : null}
            </div>
          </section>
        ) : demoMode ? (
          <section className="demo-walkthrough-card" aria-labelledby="demo-walkthrough-title">
            <CalendarCheck aria-hidden="true" />
            <div>
              <p className="eyebrow">Fixed demo scenario</p>
              <h3 id="demo-walkthrough-title">Acme renewal approval walkthrough</h3>
              <ul className="walkthrough-steps">
                <li><strong>3 automatic reads</strong><span>Web search, customer lookup, and calendar availability.</span></li>
                <li><strong>4 human decisions</strong><span>Calendar event, email, customer update, and purchase order.</span></li>
              </ul>
            </div>
          </section>
        ) : (
          <>
            <fieldset>
              <legend>Choose a starting point</legend>
              <div className="template-grid">
                {TEMPLATES.map((template) => {
                  const Icon = template.icon;
                  return (
                    <label key={template.id} className={`template-card ${templateId === template.id ? "is-selected" : ""}`}>
                      <input
                        type="radio"
                        name="template"
                        value={template.id}
                        checked={templateId === template.id}
                        onChange={() => {
                          setTemplateId(template.id);
                          setInstruction(template.instruction);
                        }}
                      />
                      <Icon aria-hidden="true" />
                      <strong>{template.title}</strong>
                      <span>{template.description}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <label className="field">
              <span>What would you like Relay to accomplish?</span>
              <textarea
                value={instruction}
                onChange={(event) => {
                  setInstruction(event.target.value);
                  setTemplateId("custom");
                }}
                rows={5}
                maxLength={4_000}
                placeholder="Describe the outcome, people involved, and any constraints."
              />
              <small>{instruction.length.toLocaleString()} / 4,000 characters</small>
            </label>
          </>
        )}
        {serverMode === "live" ? (
          <div className="approval-reminder">
            <Plus aria-hidden="true" />
            <p>
              <strong>You stay in control.</strong> Search and read steps can continue automatically. Messages,
              calendar changes, purchases, and record updates wait for your decision.
            </p>
          </div>
        ) : null}
        {mutation.isError ? <div className="inline-error" role="alert">{toUserMessage(mutation.error)}</div> : null}
        <button className="sr-only" type="submit">Submit workflow</button>
      </form>
    </Dialog>
  );
}
