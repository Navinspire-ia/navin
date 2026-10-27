// Desktop smoke test template: runs against the static frontend served locally.
// Run: BASE_URL=http://localhost:4173 npx playwright test smoke.spec.ts
import { test, expect } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:4173";

test("app starts without console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.goto(BASE_URL);
  await expect(page.locator("body")).toBeVisible();
  await expect(page.locator("#root, #app, #__next, main").first()).not.toBeEmpty();
  expect(errors).toEqual([]);
});

test("assets load with relative paths", async ({ page }) => {
  const failed: string[] = [];
  page.on("requestfailed", (r) => failed.push(r.url()));
  await page.goto(BASE_URL);
  await page.waitForLoadState("networkidle");
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
