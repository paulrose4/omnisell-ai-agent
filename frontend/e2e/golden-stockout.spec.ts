import { expect, test, type APIResponse, type Browser, type BrowserContext, type Page } from "@playwright/test";

const frontendBaseUrl = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
const apiBaseUrl = process.env.PLAYWRIGHT_API_BASE_URL ?? "http://localhost:8080";

type JsonRecord = Record<string, any>;

type Identity = {
  context: BrowserContext;
  page: Page;
  token: string;
};

async function loginAs(
  browser: Browser,
  persona: "local-owner" | "local-operator" | "local-approver" | "local-approver-2",
  buttonName: string,
  expectedEmail: string,
): Promise<Identity> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${frontendBaseUrl}/signin`);
  await page.getByRole("button", { name: new RegExp(`^${buttonName}`) }).click();
  await page.waitForURL(`${frontendBaseUrl}/dashboard`);
  await expect(page.getByRole("heading", { name: "经营总览", level: 1 })).toBeVisible();

  const sessionResponse = await context.request.get(`${frontendBaseUrl}/api/auth/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = (await sessionResponse.json()) as JsonRecord;
  expect(session.user?.email).toBe(expectedEmail);
  expect(typeof session.accessToken).toBe("string");
  expect(session.accessToken.length).toBeGreaterThan(20);

  await test.info().attach(`${persona}-identity`, {
    body: JSON.stringify({ persona, email: expectedEmail }),
    contentType: "application/json",
  });
  return { context, page, token: session.accessToken };
}

async function api(
  identity: Identity,
  method: string,
  path: string,
  options: { data?: JsonRecord; headers?: Record<string, string> } = {},
): Promise<APIResponse> {
  return identity.context.request.fetch(`${apiBaseUrl}${path}`, {
    method,
    data: options.data,
    headers: {
      Authorization: `Bearer ${identity.token}`,
      ...options.headers,
    },
  });
}

async function json(response: APIResponse, expectedStatus = 200): Promise<JsonRecord> {
  const payload = (await response.json()) as JsonRecord;
  expect(response.status(), JSON.stringify(payload)).toBe(expectedStatus);
  return payload;
}

async function waitForTask(identity: Identity, taskId: string): Promise<JsonRecord> {
  let task: JsonRecord = {};
  await expect
    .poll(
      async () => {
        task = await json(await api(identity, "GET", `/api/tasks/${encodeURIComponent(taskId)}`));
        return task.status;
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe("succeeded");
  return task;
}

test("critical stockout closes through distinct OIDC identities and asynchronous verification", async ({ browser }) => {
  const identities: Identity[] = [];
  try {
    const owner = await loginAs(browser, "local-owner", "Owner", "owner@local.omnisell.test");
    const operator = await loginAs(browser, "local-operator", "Operator", "operator@local.omnisell.test");
    const approverA = await loginAs(browser, "local-approver", "Approver A", "approver@local.omnisell.test");
    const approverB = await loginAs(
      browser,
      "local-approver-2",
      "Approver B",
      "approver-b@local.omnisell.test",
    );
    identities.push(owner, operator, approverA, approverB);

    const reset = await json(await api(owner, "POST", "/api/mock-scenarios/reset"));
    expect(reset.verified_amazon_data).toBe(false);
    expect(reset.seeded?.data_label).toBe("MOCK AMAZON DATA - NOT VERIFIED AMAZON DATA");

    const connections = await json(await api(owner, "GET", "/api/channels/amazon/connections"));
    let connection = connections.items.find((item: JsonRecord) => item.status === "active");
    if (!connection) {
      const authorization = await json(
        await api(owner, "POST", "/api/channels/amazon/authorize", {
          data: {
            selling_partner_id: "A1LOCALMOCKSELLER",
            marketplace_ids: ["ATVPDKIKX0DER"],
          },
        }),
      );
      const callback = await json(await owner.context.request.get(authorization.authorization_url));
      connection = { id: callback.connection_id, status: callback.status };
    }
    expect(connection).toBeTruthy();

    const sync = await json(
      await api(operator, "POST", "/api/sync/jobs", {
        data: { connection_id: connection.id, resources: ["orders", "inventory", "listings"] },
      }),
      202,
    );
    await waitForTask(operator, sync.task_id);

    let issue: JsonRecord | undefined;
    await expect
      .poll(
        async () => {
          const issues = await json(
            await api(operator, "GET", "/api/operations/issues?seller_sku=MOCK-STOCKOUT"),
          );
          issue = issues.items.find((item: JsonRecord) => item.issue_type === "inventory_stockout_risk");
          return Boolean(issue);
        },
        { timeout: 90_000, intervals: [500, 1000, 2000] },
      )
      .toBe(true);
    expect(issue).toBeTruthy();
    const stockoutIssue = issue as JsonRecord;
    expect(
      stockoutIssue.evidence.some(
        (item: JsonRecord) => item.data_source === "mock_amazon" && item.verified_amazon_data === false,
      ),
    ).toBe(true);

    let action: JsonRecord | undefined;
    await expect
      .poll(
        async () => {
          const detail = await json(
            await api(
              operator,
              "GET",
              `/api/operations/issues/${encodeURIComponent(stockoutIssue.id)}/detail`,
            ),
          );
          action = detail.recommended_actions.find(
            (item: JsonRecord) =>
              item.action_type === "review_replenishment" && item.status === "proposed",
          );
          return Boolean(action);
        },
        { timeout: 90_000, intervals: [500, 1000, 2000] },
      )
      .toBe(true);
    expect(action).toBeTruthy();
    const proposedAction = action as JsonRecord;

    const workItems = await json(
      await api(operator, "GET", "/api/work-items?view=unassigned&page=1&page_size=200"),
    );
    const workItem = workItems.items.find(
      (item: JsonRecord) =>
        item.operational_issue_id === stockoutIssue.id &&
        item.recommended_action_id === proposedAction.id,
    );
    expect(workItem).toBeTruthy();
    await json(
      await api(operator, "POST", "/api/work-items/bulk", {
        data: { work_item_ids: [workItem.id], operation: "assign", assignee_id: "local-operator" },
      }),
    );

    await json(
      await api(operator, "PATCH", `/api/operations/issues/${encodeURIComponent(stockoutIssue.id)}`, {
        data: { status: "acknowledged", note: "Claimed through Playwright golden acceptance." },
      }),
    );
    await json(
      await api(operator, "PATCH", `/api/operations/issues/${encodeURIComponent(stockoutIssue.id)}`, {
        data: { status: "in_progress", note: "Deterministic evidence and quantity reviewed." },
      }),
    );

    const submitted = await json(
      await api(
        operator,
        "POST",
        `/api/operations/issues/${encodeURIComponent(stockoutIssue.id)}/approval-requests`,
        {
          data: {
            connection_id: connection.id,
            recommended_action_id: proposedAction.id,
            note: "Request two independent approvals for the exact Mock execution plan.",
          },
        },
      ),
      201,
    );
    const requestId = submitted.approval_request.request_id as string;
    expect(submitted.approval_request.payload.decision_context.provenance.verified_amazon_data).toBe(false);

    const selfApproval = await json(
      await api(operator, "POST", `/api/admin/approvals/${encodeURIComponent(requestId)}/approve`, {
        data: { reason: "The requester must not approve this action." },
      }),
      403,
    );
    const requesterDenial = selfApproval.detail?.error ?? selfApproval.error;
    expect(["forbidden", "self_approval_forbidden"]).toContain(requesterDenial);

    const first = await json(
      await api(approverA, "POST", `/api/admin/approvals/${encodeURIComponent(requestId)}/approve`, {
        data: { reason: "Inventory evidence and deterministic quantity reviewed." },
      }),
    );
    expect(first.status).toBe("awaiting_second_approval");

    const second = await json(
      await api(approverB, "POST", `/api/admin/approvals/${encodeURIComponent(requestId)}/approve`, {
        data: { reason: "Independent review confirms the bounded manual execution plan." },
      }),
    );
    expect(second.status).toBe("approved");
    expect(second.operational_issue.status).toBe("approved_for_manual_execution");

    const executed = await json(
      await api(operator, "POST", "/api/mock-seller-central/execute", {
        headers: { "Idempotency-Key": `playwright-golden-${requestId}` },
        data: {
          approval_request_id: requestId,
          notes: "Recorded the approved Mock replenishment plan.",
          execution_evidence: { source: "playwright_mock_seller_central" },
        },
      }),
    );
    expect(executed.event_accepted).toBe(true);
    expect(executed.verified_amazon_data).toBe(false);

    let finalIssue: JsonRecord = {};
    await expect
      .poll(
        async () => {
          finalIssue = await json(
            await api(operator, "GET", `/api/operations/issues/${encodeURIComponent(stockoutIssue.id)}`),
          );
          return finalIssue.status;
        },
        { timeout: 120_000, intervals: [1000, 2000, 3000] },
      )
      .toBe("resolved");
    expect(finalIssue.status, "resolved").toBe("resolved");
    expect(finalIssue.resolution_code).toBe("post_sync_verified");

    const execution = await json(
      await api(operator, "GET", `/api/manual-executions/${encodeURIComponent(executed.execution_id)}`),
    );
    expect(execution.verification_status, "verified").toBe("verified");
    expect(execution.execution_evidence.mock_seller_central).toBe(true);
    expect(execution.execution_evidence.verified_amazon_data).toBe(false);

    const finalWorkItem = await json(
      await api(operator, "GET", `/api/work-items/${encodeURIComponent(workItem.id)}`),
    );
    expect(finalWorkItem.status, "completed").toBe("completed");
    expect(finalWorkItem.completed_at).toBeTruthy();
    expect(
      finalWorkItem.evidence_manifest.every((item: JsonRecord) => item.verified_amazon_data !== true),
    ).toBe(true);

    await operator.page.goto(`${frontendBaseUrl}/issues`);
    await operator.page.getByRole("button", { name: "历史", exact: true }).click();
    await expect(operator.page.getByText("MOCK-STOCKOUT").first()).toBeVisible();
    await approverA.page.goto(`${frontendBaseUrl}/approvals`);
    await expect(approverA.page).toHaveURL(`${frontendBaseUrl}/approvals`);
    await owner.page.goto(`${frontendBaseUrl}/mock-seller-central`);
    await expect(owner.page).toHaveURL(`${frontendBaseUrl}/mock-seller-central`);
  } finally {
    await Promise.all(identities.map((identity) => identity.context.close()));
  }
});
