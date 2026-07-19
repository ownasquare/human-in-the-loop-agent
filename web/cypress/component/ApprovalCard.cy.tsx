import { mount } from "cypress/react";
import { ApprovalCard } from "../../src/components/ApprovalCard";
import type { ApprovalProposal } from "../../src/types";

const approval: ApprovalProposal = {
  id: "approval-1",
  run_id: "run-1",
  step_id: "step-2",
  interrupt_id: "interrupt-1",
  version: 3,
  payload_hash: "sha256:bound-payload",
  tool_name: "email_send",
  action_label: "Send renewal review email",
  description: "Send the prepared renewal note to the customer contact.",
  rationale: "The account review is ready for scheduling.",
  consequence: "An email will be sent to alex@northstar.example.",
  risk_level: "external_write",
  status: "pending",
  allowed_decisions: ["approve", "reject", "revise"],
  payload: {
    to: "alex@northstar.example",
    subject: "Northstar renewal review",
    body: "Could we review your renewal plan next Tuesday?",
  },
  created_at: new Date().toISOString(),
};

describe("ApprovalCard", () => {
  it("shows the exact consequence and returns the bound proposal for review", () => {
    const onReview = cy.stub().as("onReview");

    mount(<ApprovalCard approval={approval} onReview={onReview} />);

    cy.contains("h3", "Send renewal review email").should("be.visible");
    cy.contains("An email will be sent to alex@northstar.example.").should("be.visible");
    cy.contains("alex@northstar.example").should("be.visible");
    cy.contains("button", /review request/i).click();
    cy.get("@onReview").should("have.been.calledOnceWith", approval);
  });
});
