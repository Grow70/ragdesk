/** Run only via backend/tests/test_frontend_auth.py against an isolated real DB/API. */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("alice");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.E2E_PASSWORD!);
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await expect(page.getByText("演示成员", { exact: true })).toBeVisible();
}

test("real Argon2 login and current authorized libraries, selection, refresh and logout", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.screenshot({
    path: "../artifacts/validation/step26a/login.png",
    fullPage: true,
  });
  await login(page);
  await expect(
    page.getByRole("button", { name: /A · 团队资料/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /个人演示空间/ }),
  ).toBeVisible();
  await expect(page.getByText("B · 隔离资料")).toHaveCount(0);
  await page.getByRole("button", { name: /A · 团队资料/ }).click();
  await expect(
    page.getByRole("heading", { name: "A · 团队资料" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/validation/step26a/workspace.png",
    fullPage: true,
  });
  const response = await request.post("/api/auth/session", {
    data: { login_name: "alice", password: process.env.E2E_PASSWORD },
  });
  expect(response.ok()).toBe(true);
  const { access_token: token } = await response.json();
  const forbidden = await request.get(
    `/api/knowledge-bases/${process.env.E2E_FOREIGN_KB}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(forbidden.status()).toBe(404);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await login(page);
  await page.getByRole("button", { name: /退出登录/ }).click();
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
});

test("real backend rejects a correctly signed expired JWT and UI clears identity", async ({
  page,
}) => {
  await login(page);
  await expect(
    page.getByRole("button", { name: /A · 团队资料/ }),
  ).toBeVisible();
  // Fault injection changes only this request credential, not the backend response.
  await page.route("**/api/knowledge-bases", async (route) => {
    await route.continue({
      headers: {
        ...route.request().headers(),
        authorization: `Bearer ${process.env.E2E_EXPIRED_TOKEN}`,
      },
    });
  });
  const rejected = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/knowledge-bases") &&
      response.status() === 401,
  );
  await page.getByRole("button", { name: /刷新列表/ }).click();
  await rejected;
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("登录已过期或失效");
  await expect(page.getByText("演示成员", { exact: true })).toHaveCount(0);
});

test("real login TTL expires in the UI under a controlled browser clock", async ({
  page,
}) => {
  await page.clock.install();
  await login(page);
  await expect(
    page.getByRole("button", { name: /A · 团队资料/ }),
  ).toBeVisible();
  await page.clock.fastForward(60_000);
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("登录已过期");
});
