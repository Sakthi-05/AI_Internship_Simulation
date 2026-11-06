import { randomUUID } from "node:crypto";
import { test, expect } from "@playwright/test";

function summarizeApiResponse(path, status, body) {
  const summary = { path, status };
  if (!body || typeof body !== "object") return summary;

  if (path === "/api/auth/register" || path === "/api/auth/login") {
    summary.student_id = body.student_id;
    summary.role = body.role;
    summary.email = body.email;
    summary.token_received = typeof body.access_token === "string";
  } else if (path === "/api/students/me") {
    summary.profile_complete = body.profile_complete;
    summary.assessed = body.assessed;
    summary.skill_count = Array.isArray(body.skill_ratings) ? body.skill_ratings.length : null;
  } else if (path === "/api/assessments/start") {
    summary.attempt_id = body.id;
    summary.status = body.status;
    summary.question_count = Array.isArray(body.questions) ? body.questions.length : null;
    summary.question_types = Array.isArray(body.questions)
      ? [...new Set(body.questions.map((question) => question.question_type))]
      : [];
    summary.generation_source = body.generation?.source;
  } else if (path.endsWith("/submit")) {
    summary.overall_score = body.evaluation?.overall_score;
    summary.evaluation_status = body.evaluation?.status;
    summary.profile_assessed = body.profile?.assessed;
  } else if (path === "/api/assessments/history") {
    summary.total = body.total;
    summary.limit = body.limit;
    summary.offset = body.offset;
    summary.attempt_count = Array.isArray(body.items) ? body.items.length : null;
    summary.historical_data_statuses = Array.isArray(body.items)
      ? body.items.map((item) => item.historical_data_status)
      : [];
  }
  return summary;
}

async function expectStatus(response, expectedStatus, label) {
  const status = response.status();
  const body = status === expectedStatus ? "" : await response.text().catch(() => "");
  expect(status, `${label} HTTP ${status}; response body: ${body}`).toBe(expectedStatus);
}

test("Module 1 registration, login, baseline assessment, and history", async ({ page, request }, testInfo) => {
  const unique = randomUUID();
  const email = `module1.qa.${Date.now()}.${unique.slice(0, 8)}@example.com`;
  const password = `M1Qa-${unique}-Aa7!`;
  const apiBaseUrl = process.env.M1_API_BASE_URL || "http://127.0.0.1:8000";
  const diagnostics = {
    email,
    browser: "Microsoft Edge channel (msedge)",
    requests: [],
    consoleErrors: [],
    failedRequests: [],
    httpFailures: [],
  };

  page.on("console", (message) => {
    if (message.type() === "error") diagnostics.consoleErrors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    diagnostics.failedRequests.push({
      method: request.method(),
      url: request.url(),
      error: request.failure()?.errorText || "request failed",
    });
  });
  page.on("response", async (response) => {
    const request = response.request();
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/")) return;

    const record = { method: request.method(), path: url.pathname, status: response.status() };
    if (response.status() >= 400) {
      record.body = (await response.text().catch(() => "")).slice(0, 2000);
      diagnostics.httpFailures.push(record);
    } else if ([
      "/api/auth/register",
      "/api/auth/login",
      "/api/students/me",
      "/api/assessments/start",
      "/api/assessments/history",
    ].includes(url.pathname) || url.pathname.endsWith("/submit")) {
      const body = await response.json().catch(() => null);
      diagnostics.requests.push(summarizeApiResponse(url.pathname, response.status(), body));
    }
  });

  try {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: /Step into your AI-powered internship/i })).toBeVisible();

    await page.getByRole("button", { name: "Create account", exact: true }).first().click();
    await page.getByLabel("Full name").fill("Module 1 QA Verification");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);

    const registrationResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/auth/register" && response.request().method() === "POST",
    );
    const profileResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/students/me" && response.request().method() === "GET",
    );
    await page.locator("form").first().getByRole("button", { name: "Create account", exact: true }).click();
    const registered = await registrationResponse;
    await expectStatus(registered, 201, "Registration");
    diagnostics.requests.push({ method: "POST", path: "/api/auth/register", status: registered.status() });
    const authenticatedProfile = await profileResponse;
    await expectStatus(authenticatedProfile, 200, "Authenticated student profile");
    await expect(page.getByText("Your profile", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("01-registration-profile.png"), fullPage: true });

    const duplicateResponse = await request.post(`${apiBaseUrl}/api/auth/register`, {
      data: { email, password, full_name: "Module 1 QA Verification" },
    });
    diagnostics.requests.push({ method: "POST", path: "/api/auth/register (duplicate)", status: duplicateResponse.status() });
    await expectStatus(duplicateResponse, 409, "Duplicate registration");

    await page.getByLabel("Full name").fill("Module 1 QA Verification");
    await page.getByLabel("College").fill("QA College");
    await page.getByLabel("Degree").fill("Computer Science");
    await page.getByLabel("Career goal").fill("Build reliable software systems");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect(page.getByText("Technical domain profile", { exact: true })).toBeVisible();

    const domainSelect = page.getByLabel("Technical domain");
    await expect(domainSelect.locator("option")).toHaveCount(6);
    await domainSelect.selectOption({ index: 1 });
    const familiarityRatings = page.locator('select[aria-label$="self-reported familiarity from 1 to 5"]');
    const firstDomainSkills = await page.locator('select[aria-label$="self-reported familiarity from 1 to 5"]').evaluateAll((items) => items.map((item) => item.getAttribute("aria-label")));
    await domainSelect.selectOption({ index: 2 });
    const secondDomainSkills = await page.locator('select[aria-label$="self-reported familiarity from 1 to 5"]').evaluateAll((items) => items.map((item) => item.getAttribute("aria-label")));
    expect(secondDomainSkills).not.toEqual(firstDomainSkills);
    const ratingCount = await familiarityRatings.count();
    expect(ratingCount).toBeGreaterThan(0);
    for (let index = 0; index < ratingCount; index += 1) {
      await familiarityRatings.nth(index).selectOption("3");
    }
    await page.getByRole("button", { name: "Save technical profile" }).click();
    await expect(page.getByText("Baseline assessment", { exact: true })).toBeVisible();
    await expect(page.getByText("Skill inventory", { exact: true })).toHaveCount(0);

    const startResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/assessments/start" && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start assessment" }).click();
    const started = await startResponse;
    await expectStatus(started, 200, "Assessment start");
    const attempt = await started.json();
    expect(attempt.questions?.length).toBeGreaterThan(0);
    const selectedTechnologyNames = secondDomainSkills.map((label) => label.match(/^(.+?) self-reported/)[1]);
    expect(attempt.questions.every((question) => selectedTechnologyNames.some((name) => question.prompt.toLowerCase().includes(name.toLowerCase())))).toBeTruthy();

    const assessmentForm = page.locator("form").filter({
      has: page.getByRole("button", { name: "Submit assessment" }),
    });
    const radioNames = await assessmentForm.locator('input[type="radio"]').evaluateAll((inputs) =>
      [...new Set(inputs.map((input) => input.name))],
    );
    for (const name of radioNames) {
      await assessmentForm.locator(`input[type="radio"][name="${name}"]`).first().check();
    }
    const shortAnswers = assessmentForm.locator("textarea");
    for (let index = 0; index < await shortAnswers.count(); index += 1) {
      await shortAnswers.nth(index).fill(
        "I would verify the requirement, inspect the relevant evidence, identify the cause, and validate a focused fix with a test.",
      );
    }

    const submitResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === `/api/assessments/${attempt.id}/submit` && response.request().method() === "POST",
    );
    await assessmentForm.getByRole("button", { name: "Submit assessment" }).click();
    const submitted = await submitResponse;
    await expectStatus(submitted, 200, "Assessment submission");

    const historySection = page.locator('[aria-labelledby="assessment-history-heading"]');
    await expect(historySection).toBeVisible();
    await expect(historySection.getByRole("button", { name: /Overall score:/ })).toHaveCount(1);
    await expect(historySection.locator('button[aria-pressed="true"]')).toHaveCount(1);
    await expect(historySection.getByText(/Historical data: snapshot/)).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("02-assessment-history.png"), fullPage: true });

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByLabel("Email")).toBeVisible();
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);
    const loginResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/auth/login" && response.request().method() === "POST",
    );
    await page.locator("form").first().getByRole("button", { name: "Sign in", exact: true }).click();
    const loggedIn = await loginResponse;
    await expectStatus(loggedIn, 200, "Login");
    await expect(page.locator('[aria-labelledby="assessment-history-heading"]')).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("03-after-login.png"), fullPage: true });
  } finally {
    await testInfo.attach("module1-browser-diagnostics.json", {
      body: Buffer.from(JSON.stringify(diagnostics, null, 2)),
      contentType: "application/json",
    });
    console.log(`Module 1 QA email: ${email}`);
    console.log(`Module 1 API diagnostics: ${JSON.stringify(diagnostics)}`);
  }
});
