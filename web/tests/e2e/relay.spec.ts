import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const timestamp = "2026-07-18T18:00:00.000Z";
const payloadHash = "4".repeat(64);

function pendingApproval() {
  return {
    id: "approval-1",
    run_id: "run-1",
    step_id: "step-2",
    interrupt_id: "interrupt-1",
    version: 4,
    payload_hash: payloadHash,
    status: "pending",
    allowed_decisions: ["approve", "reject", "revise"],
    title: "Send renewal review email",
    reason: "The account research is complete and the review needs scheduling.",
    expected_effect: "An email will be sent to alex@northstar.example.",
    review_context: {
      sender_identity: "renewals@relay.example",
      attendee_notifications: "all",
    },
    risk_level: "external_write",
    action: {
      tool_name: "email_send",
      sender: "renewals@relay.example",
      to: ["alex@northstar.example"],
      cc: [],
      subject: "Northstar renewal review",
      body: "Could we review your renewal plan next Tuesday?",
    },
    created_at: timestamp,
  };
}

function runAggregate(status = "awaiting_approval", approval: ReturnType<typeof pendingApproval> | null = pendingApproval()) {
  return {
    run: {
      id: "run-1",
      title: "Prepare Northstar renewal outreach" as string | null,
      instruction: "Research Northstar Labs, schedule a review, send the email, and update the account.",
      status,
      summary: "Research is complete. The customer email is ready for review.",
      created_at: timestamp,
      updated_at: timestamp,
      current_step: status === "awaiting_approval" ? 2 : 4,
      total_steps: 4,
      mode: "demo",
    },
    steps: [
      {
        id: "step-1",
        run_id: "run-1",
        position: 0,
        status: "succeeded",
        description: "Review the customer account",
        result_summary: "Renewal date and account owner confirmed.",
        action: { tool_name: "database_query", title: "Review the customer account" },
      },
      {
        id: "step-2",
        run_id: "run-1",
        position: 1,
        status: status === "awaiting_approval" ? "awaiting_approval" : "rejected",
        description: "Send the prepared renewal email",
        risk_level: "external_write",
        action: { tool_name: "email_send", title: "Send the prepared renewal email" },
      },
      {
        id: "step-3",
        run_id: "run-1",
        position: 2,
        status: status === "awaiting_approval" ? "pending" : "succeeded",
        description: "Record the decision",
        action: { tool_name: "database_update", title: "Record the decision" },
      },
    ],
    pending_approval: approval,
    audit_events: [
      {
        id: "audit-1",
        run_id: "run-1",
        event_type: "approval_requested",
        outcome: "pending",
        summary: approval ? "Email action paused for a human decision." : "Decision recorded.",
        actor: "agent",
        occurred_at: timestamp,
        hash: "sha256:audit-1",
      },
    ],
    receipts: [],
  };
}

interface MockApiState {
  aggregate: ReturnType<typeof runAggregate>;
  decisions: Array<Record<string, unknown>>;
  runCreates: Array<Record<string, unknown>>;
}

interface MockApiOptions {
  capabilitiesError?: boolean;
  capabilitiesGate?: Promise<void>;
  staleDecisionOnce?: boolean;
}

async function installMockApi(page: Page, options: MockApiOptions = {}): Promise<MockApiState> {
  const state: MockApiState = { aggregate: runAggregate(), decisions: [], runCreates: [] };
  let staleDecisionRemaining = options.staleDecisionOnce ?? false;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/v1/capabilities") {
      await options.capabilitiesGate;
      if (options.capabilitiesError) {
        await route.fulfill({ status: 503, json: { error: { code: "not_ready", message: "Unavailable" } } });
        return;
      }
      await route.fulfill({ json: { application: "Relay", mode: "demo", demo_mode: true, write_tools_require_approval: true } });
      return;
    }
    if (path === "/api/v1/connectors") {
      await route.fulfill({ json: { items: [
        { id: "search", name: "Web search", category: "search", status: "ready", mode: "demo", capabilities: ["search"] },
        { id: "email", name: "Email", category: "email", status: "demo", mode: "demo", capabilities: ["draft", "send with approval"] },
      ] } });
      return;
    }
    if (path === "/api/v1/audit-events") {
      await route.fulfill({ json: { items: state.aggregate.audit_events, total: state.aggregate.audit_events.length } });
      return;
    }
    if (path === "/api/v1/approvals") {
      await route.fulfill({ json: { items: state.aggregate.pending_approval ? [state.aggregate.pending_approval] : [], total: state.aggregate.pending_approval ? 1 : 0 } });
      return;
    }
    if (path === "/api/v1/runs" && method === "GET") {
      await route.fulfill({ json: { items: [state.aggregate.run], total: 1 } });
      return;
    }
    if (path === "/api/v1/runs" && method === "POST") {
      const input = request.postDataJSON() as { instruction: string; mode: string };
      state.runCreates.push(input);
      const created = runAggregate("running", null);
      created.run = { ...created.run, id: "created-run", title: null, instruction: input.instruction, status: "running", current_step: 1 };
      created.steps = created.steps.map((step) => ({ ...step, run_id: "created-run" }));
      state.aggregate = created;
      await route.fulfill({ status: 201, json: created });
      return;
    }
    if (path.startsWith("/api/v1/runs/") && method === "GET") {
      await route.fulfill({ json: state.aggregate });
      return;
    }
    if (path === "/api/v1/approvals/approval-1/decisions" && method === "POST") {
      const decision = request.postDataJSON() as Record<string, unknown>;
      if (staleDecisionRemaining) {
        staleDecisionRemaining = false;
        state.aggregate = runAggregate("awaiting_approval", {
          ...pendingApproval(),
          version: 5,
          payload_hash: "5".repeat(64),
        });
        await route.fulfill({
          status: 409,
          json: { error: { code: "stale_approval", message: "Approval changed." } },
        });
        return;
      }
      state.decisions.push(decision);
      if (decision.decision === "revise") {
        const revisedAction = decision.revised_action as ReturnType<typeof pendingApproval>["action"];
        const nextApproval = {
          ...pendingApproval(),
          id: "approval-2",
          version: 5,
          payload_hash: "5".repeat(64),
          action: revisedAction,
        };
        state.aggregate = runAggregate("awaiting_approval", nextApproval);
        await route.fulfill({ json: state.aggregate });
        return;
      }
      const nextStatus = decision.decision === "reject" ? "completed_with_rejections" : "completed";
      state.aggregate = runAggregate(nextStatus, null);
      await route.fulfill({ json: state.aggregate });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: { code: "not_found", message: `No mock for ${method} ${path}` } } });
  });
  return state;
}

test("truthfully starts the single fixed demo walkthrough", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Start workflow" }).click();
  const createDialog = page.getByRole("dialog", { name: "Start the demo walkthrough" });
  await expect(createDialog).toBeVisible();
  await expect(createDialog.getByText("Acme renewal approval walkthrough")).toBeVisible();
  await expect(createDialog.getByText("4 human decisions")).toBeVisible();
  await expect(createDialog.getByLabel("What would you like Relay to accomplish?")).toHaveCount(0);
  await createDialog.getByRole("button", { name: "Start walkthrough" }).click();

  await expect(page).toHaveURL(/\/runs\/created-run$/);
  await expect(page.getByRole("heading", { name: "Prepare the Acme renewal workflow" })).toBeVisible();
  await expect(page.getByText("Plan and activity")).toBeVisible();
  expect(state.runCreates).toEqual([
    { instruction: "Prepare the Acme renewal workflow", mode: "demo" },
  ]);
});

test("cannot start while workspace mode is unknown or unavailable", async ({ page }) => {
  let releaseCapabilities = () => {};
  const capabilitiesGate = new Promise<void>((resolve) => {
    releaseCapabilities = resolve;
  });
  const state = await installMockApi(page, { capabilitiesGate });
  await page.goto("/");

  await page.getByRole("button", { name: "Start workflow" }).click();
  const pendingDialog = page.getByRole("dialog", { name: "Start a workflow" });
  await expect(pendingDialog.getByRole("heading", { name: "Confirming workspace mode" })).toBeVisible();
  await expect(pendingDialog.getByRole("button", { name: "Waiting for workspace" })).toBeDisabled();
  expect(state.runCreates).toHaveLength(0);

  releaseCapabilities();
  const demoDialog = page.getByRole("dialog", { name: "Start the demo walkthrough" });
  await expect(demoDialog.getByRole("button", { name: "Start walkthrough" })).toBeEnabled();
  expect(state.runCreates).toHaveLength(0);
});

test("capabilities failure stays fail-closed and offers retry", async ({ page }) => {
  const state = await installMockApi(page, { capabilitiesError: true });
  await page.goto("/");

  await page.getByRole("button", { name: "Start workflow" }).click();
  const dialog = page.getByRole("dialog", { name: "Start a workflow" });
  await expect(dialog.getByRole("heading", { name: "Workspace mode is unavailable" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Waiting for workspace" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Try again" })).toBeVisible();
  expect(state.runCreates).toHaveLength(0);
});

test("requires a reason and a separate confirmation before rejection", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/approvals");

  await page.getByRole("button", { name: "Review request" }).click();
  const reviewDialog = page.getByRole("dialog", { name: "Send renewal review email" });
  await expect(reviewDialog.getByText("An email will be sent to alex@northstar.example.")).toBeVisible();
  const reject = reviewDialog.getByRole("button", { name: "Reject", exact: true });
  await expect(reject).toBeFocused();
  await reject.click();

  const reason = page.getByLabel("Rejection reason (required)");
  const confirm = page.getByRole("button", { name: "Confirm rejection" });
  await expect(reason).toBeFocused();
  await expect(confirm).toBeDisabled();
  await reason.fill("The recipient must be corrected before this email is sent.");
  await expect(confirm).toBeEnabled();
  await confirm.click();

  await expect(page.getByRole("heading", { name: "You are all caught up" })).toBeVisible();
  expect(state.decisions).toHaveLength(1);
  expect(state.decisions[0]).toMatchObject({
    decision: "reject",
    expected_version: 4,
    expected_payload_hash: payloadHash,
    reason: "The recipient must be corrected before this email is sent.",
  });
  expect(state.decisions[0].idempotency_key).toEqual(expect.any(String));
});

test("closes and refreshes a stale approval instead of allowing repeat submission", async ({ page }) => {
  const state = await installMockApi(page, { staleDecisionOnce: true });
  await page.goto("/approvals");

  await page.getByRole("button", { name: "Review request" }).click();
  const staleDialog = page.getByRole("dialog", { name: "Send renewal review email" });
  await staleDialog.getByRole("button", { name: "Approve action" }).click();
  await expect(staleDialog).not.toBeVisible();
  expect(state.decisions).toHaveLength(0);

  await expect(page.getByRole("button", { name: "Review request" })).toBeVisible();
  await page.getByRole("button", { name: "Review request" }).click();
  await page.getByText("Technical binding details").click();
  await expect(page.locator(".technical-details").filter({ hasText: "Technical binding details" }).getByText("5", { exact: true })).toBeVisible();
});

test("submits a typed email revision and presents the newly bound proposal", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/runs/run-1");

  await expect(page.getByRole("button", { name: "Review decision" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Review request" })).toHaveCount(1);
  await page.getByRole("button", { name: "Review request" }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const sender = page.getByLabel("Sender (set by your workspace)");
  await expect(sender).toHaveValue("renewals@relay.example");
  await expect(sender).toHaveAttribute("readonly", "");
  await page.getByLabel("Subject").fill("Updated Northstar renewal review");
  await page.getByLabel("To").fill("alex@northstar.example, finance@northstar.example");
  await page.getByRole("button", { name: "Submit revision" }).click();

  await page.getByRole("button", { name: "Review request" }).click();
  const revisedDialog = page.getByRole("dialog", { name: "Send renewal review email" });
  await expect(
    revisedDialog.locator(".decision-fields").getByText("Updated Northstar renewal review", { exact: true }),
  ).toBeVisible();
  await revisedDialog.getByText("Technical binding details").click();
  await expect(revisedDialog.locator(".technical-details").filter({ hasText: "Technical binding details" }).getByText("5", { exact: true })).toBeVisible();
  expect(state.decisions).toHaveLength(1);
  expect(state.decisions[0]).toMatchObject({
    decision: "revise",
    expected_version: 4,
    expected_payload_hash: payloadHash,
    revised_action: {
      tool_name: "email_send",
      sender: "renewals@relay.example",
      to: ["alex@northstar.example", "finance@northstar.example"],
      subject: "Updated Northstar renewal review",
    },
  });
});

test("keeps primary work areas usable on a narrow screen in light and dark modes", async ({ page }) => {
  await installMockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await expect(page.getByRole("navigation", { name: "Primary" }).last()).toBeVisible();
  const theme = page.getByLabel("Theme").last();
  await theme.selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await theme.selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByRole("link", { name: /approvals/i }).last().click();
  await expect(page.getByRole("heading", { name: "Approval requests" })).toBeVisible();
});

test("packaged app completes all four real approval gates with write readbacks", async ({ page, request }) => {
  test.skip(!process.env.PLAYWRIGHT_BASE_URL, "Runs only against the packaged FastAPI-served app.");

  const reset = await request.post("/api/v1/demo/reset");
  expect(reset.ok()).toBeTruthy();

  await page.goto("/");
  await page.getByRole("button", { name: "Start workflow" }).click();
  await page
    .getByRole("dialog", { name: "Start the demo walkthrough" })
    .getByRole("button", { name: "Start walkthrough" })
    .click();

  await expect(page).toHaveURL(/\/runs\/run_/);

  const approveGate = async (title: string, canRevise: boolean) => {
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    await expect(page.getByText("Waiting for you", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review request" })).toHaveCount(1);
    await page.getByRole("button", { name: "Review request" }).click();
    const dialog = page.getByRole("dialog", { name: title });
    await expect(dialog.getByRole("button", { name: "Reject", exact: true })).toBeFocused();
    if (canRevise) {
      await expect(dialog.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    } else {
      await expect(dialog.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    }
    await dialog.getByRole("button", { name: "Approve action" }).click();
    await expect(dialog).not.toBeVisible();
  };

  await approveGate("Create calendar event", true);
  await approveGate("Send email", true);
  await approveGate("Update customer record", false);
  await approveGate("Create purchase order", false);

  await expect(page.locator(".run-hero").getByText("Complete", { exact: true })).toBeVisible();
  await expect(page.getByText("Waiting for you")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "What happened" })).toBeVisible();

  const writeReceipts = page.locator(".receipt-list > li").filter({
    hasText: /Create a calendar event completed|Send an email completed|Update a customer record completed|Create a purchase order completed/,
  });
  await expect(writeReceipts).toHaveCount(4);
  await expect(writeReceipts.locator("details")).toHaveCount(4);
  const receiptHeadlines = await page.locator(".receipt-list strong").allTextContents();
  expect(receiptHeadlines.join(" ")).not.toMatch(/fixture:|query:|availability:|demo_/);
});
