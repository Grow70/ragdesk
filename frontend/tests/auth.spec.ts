import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const user = {
  user_id: A,
  login_name: "alice",
  display_name: "演示成员",
  request_id: "request-user",
};
const kb = (id = A, name = "团队制度") => ({
  id,
  name,
  role: "member",
  created_at: "2026-09-29T00:00:00Z",
  request_id: "request-kb",
});
const json = (route: Route, value: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
const error = (route: Route, status: number) =>
  json(
    route,
    {
      error: { code: "HTTP_ERROR", message: "Do not echo raw server details" },
      request_id: "request-error",
    },
    status,
  );
async function setup(page: Page, seconds = 1800, items = [kb()]) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/session") {
      expect(route.request().postDataJSON()).toEqual({
        login_name: "alice",
        password: "example-password",
      });
      return json(route, {
        access_token: "test-memory-token",
        token_type: "bearer",
        expires_in: seconds,
        request_id: "request-login",
      });
    }
    expect(route.request().headers().authorization).toBe(
      "Bearer test-memory-token",
    );
    if (path === "/api/auth/me") return json(route, user);
    if (path === "/api/knowledge-bases")
      return json(route, { items, request_id: "request-list" });
    if (path === `/api/knowledge-bases/${A}`) return json(route, kb());
    if (path === `/api/knowledge-bases/${B}`)
      return json(route, kb(B, "产品资料"));
    return error(route, 404);
  });
  await page.goto("/");
}
async function login(page: Page) {
  await page.getByLabel("账号", { exact: true }).fill("alice");
  await page.getByLabel("密码", { exact: true }).fill("example-password");
  await page.getByRole("button", { name: "登录工作空间" }).click();
}

test("JSON login, current user, authorized selection, no persistence and refresh logout", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await expect(page.getByText("演示成员", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /团队制度/ }).click();
  await expect(page.getByRole("heading", { name: "团队制度" })).toBeVisible();
  expect(
    await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
      cookies: document.cookie,
    })),
  ).toEqual({ local: [], session: [], cookies: "" });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await expect(page.getByText("演示成员", { exact: true })).toHaveCount(0);
});

test("wrong credentials stay on login, hide backend text and show request ID", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/auth/session", (route) => error(route, 401));
  await login(page);
  await expect(page.getByRole("alert")).toContainText("账号或密码错误");
  await expect(page.getByRole("alert")).toContainText("request-error");
  await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");
  await expect(page.getByText("Do not echo raw server details")).toHaveCount(0);
});

test("empty authorized list is not an error", async ({ page }) => {
  await setup(page, 1800, []);
  await login(page);
  await expect(
    page.getByRole("heading", { name: "还没有可访问的知识库" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("list failure supports retry and loading state", async ({ page }) => {
  await setup(page);
  await page.route("**/api/knowledge-bases", (route) => error(route, 503));
  await login(page);
  await expect(page.getByRole("alert")).toContainText("服务暂时不可用");
  await page.unroute("**/api/knowledge-bases");
  let release!: () => void;
  const barrier = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/knowledge-bases", async (route) => {
    await barrier;
    await json(route, { items: [kb()], request_id: "retry" });
  });
  await page.getByRole("button", { name: /刷新列表/ }).click();
  await expect(page.getByRole("status")).toContainText("正在加载你的知识库");
  release();
  await expect(page.getByRole("button", { name: /团队制度/ })).toBeVisible();
});

test("protected 401 clears the entire workspace and permits a fresh login", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await page.getByRole("button", { name: /团队制度/ }).click();
  await page.route("**/api/knowledge-bases", (route) => error(route, 401));
  await page.getByRole("button", { name: /刷新列表/ }).click();
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("登录已过期或失效");
  await expect(page.getByRole("heading", { name: "团队制度" })).toHaveCount(0);
  await page.unroute("**/api/knowledge-bases");
  await login(page);
  await expect(page.getByRole("button", { name: /团队制度/ })).toBeVisible();
});

test("controlled clock expires idle sessions without a long sleep", async ({
  page,
}) => {
  await page.clock.install();
  await setup(page, 60);
  await login(page);
  await expect(page.getByRole("button", { name: /团队制度/ })).toBeVisible();
  await page.clock.fastForward(60_000);
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("登录已过期");
});

test("revoked membership on selection never exposes a selected library", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await page.route(`**/api/knowledge-bases/${A}`, (route) => error(route, 404));
  await page.getByRole("button", { name: /团队制度/ }).click();
  await expect(page.getByRole("alert")).toContainText("不存在或你已无权访问");
  await expect(page.getByRole("heading", { name: "团队制度" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /团队制度/ })).toHaveCount(0);
  await expect(page.getByText("演示成员", { exact: true })).toBeVisible();
});

test("logout while requests are pending cannot restore the old workspace", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await expect(page.getByRole("button", { name: /团队制度/ })).toBeVisible();
  let release!: () => void;
  let entered!: () => void;
  const barrier = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route("**/api/knowledge-bases", async (route) => {
    entered();
    await barrier;
    await json(route, { items: [kb()], request_id: "late" }).catch(() => {});
  });
  await page.getByRole("button", { name: /刷新列表/ }).click();
  await started;
  await page.getByRole("button", { name: /退出登录/ }).click();
  release();
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("你已退出登录");
  await expect(page.getByRole("button", { name: /团队制度/ })).toHaveCount(0);
});

test("network failures and malformed successful bodies are clearly reported", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/auth/session", (route) => route.abort("failed"));
  await login(page);
  await expect(page.getByRole("alert")).toContainText("无法连接服务");
  await page.unroute("**/api/auth/session");
  await page.route("**/api/auth/session", (route) =>
    json(route, { access_token: "broken" }),
  );
  await login(page);
  await expect(page.getByRole("alert")).toContainText("无法识别的数据");
});

test("timeout is bounded and stops the login loading state", async ({
  page,
}) => {
  await page.clock.install();
  await setup(page);
  await page.route("**/api/auth/session", () => {});
  await login(page);
  await expect(
    page.getByRole("button", { name: /正在验证身份/ }),
  ).toBeDisabled();
  await page.clock.fastForward(15_100);
  await expect(page.getByRole("alert")).toContainText("请求超时");
  await expect(
    page.getByRole("button", { name: "登录工作空间" }),
  ).toBeEnabled();
});

test("out-of-order selections cannot replace the newest selected library", async ({
  page,
}) => {
  await setup(page, 1800, [kb(), kb(B, "产品资料")]);
  await login(page);
  let release!: () => void;
  const barrier = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/knowledge-bases/${A}`, async (route) => {
    await barrier;
    await json(route, kb());
  });
  await page.getByRole("button", { name: /团队制度/ }).click();
  await page.getByRole("button", { name: /产品资料/ }).click();
  await expect(page.getByRole("heading", { name: "产品资料" })).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "团队制度" })).toHaveCount(0);
});

test("small viewport keeps login and workspace within the screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await login(page);
  await expect(page.getByRole("button", { name: /团队制度/ })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
