import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { hash } from "../../src/session";

const raw = readFileSync(
  new URL("../../public/session.json", import.meta.url),
  "utf8",
);
test("walks through real patches, full history and keyboard navigation", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Trim surrounding whitespace" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Previous checkpoint" }),
  ).toBeDisabled();
  await expect(page.locator(".patch")).toContainText("return value.strip()");
  await expect(page.locator(".patch")).toContainText("[REDACTED]");
  await page.getByRole("button", { name: "Next checkpoint" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Normalize Unicode case" }),
  ).toBeFocused();
  await expect(page.locator(".patch")).toContainText(
    "return value.strip().casefold()",
  );
  await expect(
    page.getByRole("button", { name: "Next checkpoint" }),
  ).toBeDisabled();
  await page.getByText("Full reviewed transcript", { exact: true }).click();
  await expect(page.locator("details pre")).toContainText("Straße");
  await page.screenshot({
    path: info.outputPath("walkthrough.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test("imports a local reviewed JSON without network transfer", async ({
  page,
}) => {
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "A small normalization change" }),
  ).toBeVisible();
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  const value = JSON.parse(raw);
  value.payload.title = "My imported session";
  value.sha256 = await hash(value.payload);
  await page.locator("#session-file").setInputFiles({
    name: "reviewed.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(value)),
  });
  await expect(
    page.getByRole("heading", { name: "My imported session" }),
  ).toBeVisible();
  expect(requests).toEqual([]);
});
test("rejects altered content and retains the loaded session", async ({
  page,
}) => {
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "A small normalization change" }),
  ).toBeVisible();
  const value = JSON.parse(raw);
  value.payload.checkpoints[0].patch += "tampered";
  await page.locator("#session-file").setInputFiles({
    name: "tampered.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(value)),
  });
  await expect(page.getByRole("alert")).toHaveText("Content digest mismatch");
  await expect(
    page.getByRole("heading", { name: "A small normalization change" }),
  ).toBeVisible();
});
test("treats imported HTML and commands as inert text", async ({ page }) => {
  await page.goto("./");
  const value = JSON.parse(raw);
  value.payload.title = '<img src=x onerror="window.injected=true">';
  value.payload.checkpoints[0].patch =
    "<script>window.injected=true</script>\n/run dangerous-command";
  value.sha256 = await hash(value.payload);
  await page.locator("#session-file").setInputFiles({
    name: "literal.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(value)),
  });
  await expect(
    page.getByRole("heading", { name: value.payload.title }),
  ).toBeVisible();
  await expect(page.locator(".patch")).toContainText("/run dangerous-command");
  expect(await page.locator("#app img, #app script").count()).toBe(0);
  expect(await page.evaluate(() => Object.hasOwn(window, "injected"))).toBe(
    false,
  );
});
test("mobile layout, local assets and automated accessibility checks", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const external: string[] = [];
  await page.route("**/*", (route) => {
    if (new URL(route.request().url()).origin !== "http://127.0.0.1:4174") {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "A small normalization change" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: info.outputPath("mobile.png"),
    fullPage: true,
  });
  expect(external).toEqual([]);
});
test("rejects an oversized imported file", async ({ page }) => {
  await page.goto("./");
  await page.locator("#session-file").setInputFiles({
    name: "large.json",
    mimeType: "application/json",
    buffer: Buffer.alloc(5 * 1024 * 1024 + 1, "x"),
  });
  await expect(page.getByRole("alert")).toHaveText("Session exceeds 5 MiB");
});
test("reports a failed initial load", async ({ page }) => {
  await page.route("**/session.json", (r) => r.fulfill({ status: 404 }));
  await page.goto("./");
  await expect(page.getByRole("alert")).toHaveText(
    "The example session could not be loaded",
  );
});
