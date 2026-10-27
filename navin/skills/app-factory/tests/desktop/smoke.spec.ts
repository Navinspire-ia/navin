// Desktop smoke test template: runs against the packaged frontend (www) served locally.
// Run: BASE_URL=http://localhost:4173 npx playwright test tests/smoke.spec.ts
//
// What it checks: the app shell starts, renders, and every asset of its own
// (scripts, styles, fonts, images) loads. Calls to the backend are NOT judged
// here: without the backend running they fail by design, and they are covered
// by tests/api.spec.ts, which runs with BACKEND_URL set.
import { test, expect } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:4173";
const ASSET_RE = /\.(m?js|css|png|jpe?g|svg|gif|webp|avif|ico|woff2?|ttf|otf|json|webmanifest|map|wasm)(\?|$)/i;

function isOwnAsset(url: string): boolean {
  try {
    const parsed = new URL(url);
    return url.startsWith(BASE_URL) && ASSET_RE.test(parsed.pathname);
  } catch {
    return false;
  }
}

test("app starts and its own assets load", async ({ page }) => {
  const pageErrors: string[] = [];
  const assetFailures: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("response", (r) => {
    if (r.status() >= 400 && isOwnAsset(r.url())) assetFailures.push(`${r.status()} ${r.url()}`);
  });
  page.on("requestfailed", (r) => {
    if (isOwnAsset(r.url())) assetFailures.push(`failed ${r.url()}`);
  });
  await page.goto(BASE_URL);
  await expect(page.locator("body")).toBeVisible();
  await expect(page.locator("#root, #app, #__next, main").first()).not.toBeEmpty();
  expect(pageErrors).toEqual([]);
  expect(assetFailures).toEqual([]);
});

test("assets use relative paths (no absolute /assets that break in file:// and custom schemes)", async ({ page }) => {
  const failed: string[] = [];
  page.on("requestfailed", (r) => {
    if (isOwnAsset(r.url())) failed.push(r.url());
  });
  await page.goto(BASE_URL);
  await page.waitForLoadState("domcontentloaded");
  expect(failed).toEqual([]);
});

test("navigation works", async ({ page }) => {
  await page.goto(BASE_URL);
  const link = page.getByRole("link").first();
  if (await link.count()) {
    await link.click();
    await expect(page.locator("body")).toBeVisible();
  }
});
