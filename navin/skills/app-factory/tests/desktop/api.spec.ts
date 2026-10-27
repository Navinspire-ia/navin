// Backend reachability test template: the packaged app must reach its backend.
// Run with the backend up:
//   BASE_URL=http://localhost:4173 BACKEND_URL=https://api.example.com npx playwright test tests/api.spec.ts
//
// BACKEND_URL is required: the "api" delivery test is about the real backend,
// so the test fails plainly when it is missing instead of pretending to pass.
import { test, expect } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:4173";
const BACKEND_URL = process.env.BACKEND_URL ?? "";

test("BACKEND_URL is set", () => {
  expect(BACKEND_URL, "set BACKEND_URL to the backend the packaged app talks to").not.toEqual("");
});

test("backend answers", async ({ request }) => {
  const response = await request.get(BACKEND_URL, { failOnStatusCode: false, timeout: 15_000 });
  // Any HTTP answer proves reachability (a 401 or 404 on the root is fine); a
  // connection error, a timeout or a TLS failure throws above.
  expect(response.status()).toBeLessThan(600);
});

test("the app reaches the backend without CORS or mixed-content errors", async ({ page }) => {
  const blocked: string[] = [];
  page.on("console", (m) => {
    const text = m.text();
    if (m.type() === "error" && /CORS|Mixed Content|blocked by|net::ERR_/i.test(text)) blocked.push(text);
  });
  page.on("requestfailed", (r) => {
    if (r.url().startsWith(BACKEND_URL)) blocked.push(`${r.failure()?.errorText ?? "failed"} ${r.url()}`);
  });
  await page.goto(BASE_URL);
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
  expect(blocked).toEqual([]);
});
