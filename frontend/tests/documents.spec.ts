import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const D = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const J = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
const C = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const date = "2026-09-29T00:00:00Z";
const job = (status = "queued") => ({
  job_id: J,
  status,
  attempts: status === "queued" ? 0 : 1,
  max_attempts: 3,
  error_code: status === "failed" ? "MODEL_TIMEOUT" : null,
  error_summary: status === "failed" ? "MODEL_TIMEOUT" : null,
});
const doc = (
  status = "ready",
  task: ReturnType<typeof job> | null = job("succeeded"),
) => ({
  document_id: D,
  file_name: "报销规则.md",
  status,
  created_at: date,
  latest_job: task,
});
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
const error = (route: Route, status = 503) =>
  json(
    route,
    {
      error: { code: "ERROR", message: "private exception" },
      request_id: "request-doc-error",
    },
    status,
  );
const basePath = `/api/knowledge-bases/${A}/documents`;
async function setup(
  page: Page,
  options: {
    member?: boolean;
    docs?: ReturnType<typeof doc>[];
    states?: string[];
    total?: number;
  } = {},
) {
  let items = options.docs ?? [doc()];
  let calls = 0,
    deletes = 0,
    uploads = 0,
    rebuilds = 0;
  const bases = [A, B].map((id, i) => ({
    id,
    name: i ? "产品资料" : "团队制度",
    role: options.member ? "member" : "admin",
    created_at: date,
    request_id: "kb",
  }));
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname;
    if (path.endsWith("/auth/session"))
      return json(route, {
        access_token: "memory-token",
        token_type: "bearer",
        expires_in: 1800,
      });
    expect(req.headers().authorization).toBe("Bearer memory-token");
    if (path.endsWith("/auth/me"))
      return json(route, {
        user_id: A,
        login_name: "alice",
        display_name: "演示用户",
        request_id: "user",
      });
    if (path === "/api/knowledge-bases") return json(route, { items: bases });
    const kb = bases.find((k) => path === `/api/knowledge-bases/${k.id}`);
    if (kb) return json(route, kb);
    if (path === `/api/knowledge-bases/${B}/documents`)
      return json(route, { items: [], total: 0 });
    if (path === basePath) {
      if (req.method() === "POST") {
        uploads++;
        expect(req.headers()["content-type"]).toMatch(
          /^multipart\/form-data; boundary=/,
        );
        expect(req.postData()).toContain('name="file"');
        items = [doc("queued", job())];
        return json(
          route,
          {
            document_id: D,
            job_id: J,
            status: "uploaded",
            job_status: "queued",
          },
          202,
        );
      }
      expect(url.searchParams.get("limit")).toBe("10");
      return json(route, { items, total: options.total ?? items.length });
    }
    if (path === `${basePath}/${D}/jobs/${J}`) {
      const state =
        options.states?.[Math.min(calls, options.states.length - 1)] ??
        "queued";
      calls++;
      if (state === "error") return error(route);
      items = [
        doc(
          state === "succeeded"
            ? "ready"
            : state === "failed"
              ? "failed"
              : "processing",
          job(state),
        ),
      ];
      return json(route, job(state));
    }
    if (path === `${basePath}/${D}/rebuild`) {
      rebuilds++;
      items = [doc("ready", job())];
      return json(
        route,
        { document_id: D, job_id: J, status: "uploaded", job_status: "queued" },
        202,
      );
    }
    if (path === `${basePath}/${D}`) {
      if (req.method() === "DELETE") {
        deletes++;
        items = [];
        return json(route, {
          document_id: D,
          status: "deleted",
          cleanup_status: "removed",
        });
      }
      return json(route, items[0]);
    }
    if (path === `${basePath}/${D}/preview`)
      return json(route, {
        document_id: D,
        build_id: B,
        total_chunks: 5,
        items: [
          {
            chunk_id: C,
            ordinal: 0,
            text: "演示数据：680 元。<script>window.pwned=true</script>",
            page_number: 2,
            heading_path: ["报销"],
            start_line: null,
            end_line: null,
            truncated: true,
            locator_truncated: false,
          },
        ],
      });
    return error(route, 404);
  });
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("alice");
  await page.getByLabel("密码", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await page.getByRole("button", { name: /团队制度/ }).click();
  await page.getByRole("button", { name: "打开文档" }).click();
  await expect(page.getByRole("heading", { name: /文档资料/ })).toBeVisible();
  return {
    calls: () => calls,
    deletes: () => deletes,
    uploads: () => uploads,
    rebuilds: () => rebuilds,
  };
}

test("member is read-only; bounded preview is plain text with physical page", async ({
  page,
}) => {
  await setup(page, { member: true, docs: [doc("ready", null)] });
  await expect(page.getByText("成员 · 只读", { exact: false })).toBeVisible();
  for (const name of ["上传文件", "重建索引", "删除文档"])
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  await page.getByRole("button", { name: "切块预览" }).click();
  await expect(
    page.getByRole("heading", { name: "片段 1 · 第 2 页" }),
  ).toBeVisible();
  await expect(page.locator("pre")).toContainText("680 元。<script>");
  expect(await page.evaluate(() => "pwned" in window)).toBe(false);
  await expect(page.getByText("此片段已截断。")).toBeVisible();
});

test("upload validates empty/type/size, sends multipart and renders queued job", async ({
  page,
}) => {
  const state = await setup(page, { docs: [] });
  await expect(page.getByRole("heading", { name: "还没有文档" })).toBeVisible();
  await expect(page.getByText(/单文件最多 10 MiB/)).toBeVisible();
  await page.getByRole("button", { name: "上传文件" }).click();
  await expect(page.getByRole("alert")).toContainText("非空文件");
  for (const [name, buffer, message] of [
    ["bad.exe", Buffer.from("x"), "仅支持"],
    ["large.txt", Buffer.alloc(10 * 1024 * 1024 + 1), "超过 10 MiB"],
  ] as const) {
    await page
      .getByLabel("选择文件")
      .setInputFiles({ name, mimeType: "application/octet-stream", buffer });
    await page.getByRole("button", { name: "上传文件" }).click();
    await expect(page.getByRole("alert")).toContainText(message);
  }
  expect(state.uploads()).toBe(0);
  await page.getByLabel("选择文件").setInputFiles({
    name: "demo.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 演示数据\n680 元"),
  });
  await page.getByRole("button", { name: "上传文件" }).click();
  await expect(page.getByText("最近任务：排队中")).toBeVisible();
  expect(state.uploads()).toBe(1);
});

for (const terminal of ["succeeded", "failed"])
  test(`poll stops at ${terminal} and exposes safe failure`, async ({
    page,
  }) => {
    await page.clock.install();
    const state = await setup(page, {
      docs: [doc("queued", job())],
      states: ["running", terminal],
    });
    await expect(page.getByText("最近任务：排队中")).toBeVisible();
    await page.clock.runFor(2100);
    await expect(page.getByText("最近任务：处理中")).toBeVisible();
    await page.clock.runFor(2100);
    await expect(
      page.getByText(
        terminal === "succeeded" ? "最近任务：已完成" : "MODEL_TIMEOUT",
        { exact: true },
      ),
    ).toBeVisible();
    await page.clock.runFor(130000);
    expect(state.calls()).toBe(2);
  });

for (const leave of ["switch", "back", "logout"])
  test(`poll stops on ${leave}`, async ({ page }) => {
    await page.clock.install();
    const state = await setup(page, {
      docs: [doc("queued", job())],
      states: ["running"],
    });
    await expect(page.getByText("最近任务：排队中")).toBeVisible();
    await page.clock.runFor(2100);
    await expect(page.getByText("最近任务：处理中")).toBeVisible();
    expect(state.calls()).toBe(1);
    if (leave === "switch") {
      await page.getByLabel("当前知识库", { exact: true }).selectOption(B);
      await expect(
        page.getByRole("heading", { name: "还没有文档" }),
      ).toBeVisible();
    } else
      await page
        .getByRole("button", {
          name: leave === "back" ? "返回知识库" : "退出登录",
        })
        .click();
    await page.clock.runFor(130000);
    expect(state.calls()).toBe(1);
  });

test("bounded polling stops by deadline and can be manually restarted", async ({
  page,
}) => {
  await page.clock.install();
  const state = await setup(page, { docs: [doc("queued", job())] });
  await expect(page.getByText("最近任务：排队中")).toBeVisible();
  // fastForward exercises the independent wall-clock deadline even if network is slow.
  await page.clock.fastForward(120100);
  await expect(
    page.getByRole("button", { name: "重新检查任务" }),
  ).toBeVisible();
  const stopped = state.calls();
  expect(stopped).toBeLessThanOrEqual(60);
  await page.clock.runFor(10000);
  expect(state.calls()).toBe(stopped);
  await page.getByRole("button", { name: "重新检查任务" }).click();
  await page.clock.runFor(2100);
  expect(state.calls()).toBe(stopped + 1);
});

test("poll error stops, does not leak raw backend text, and manual retry recovers", async ({
  page,
}) => {
  await page.clock.install();
  const state = await setup(page, {
    docs: [doc("queued", job())],
    states: ["error", "succeeded"],
  });
  await page.clock.runFor(2100);
  await expect(page.getByRole("alert")).toContainText("request-doc-error");
  await expect(page.getByText("private exception")).toHaveCount(0);
  await page.clock.runFor(20000);
  expect(state.calls()).toBe(1);
  await page.getByRole("button", { name: "重新检查任务" }).click();
  await page.clock.runFor(2100);
  await expect(page.getByText("最近任务：已完成")).toBeVisible();
});

test("delete requires named confirmation; cancel does not write; rebuild keeps ready", async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "删除文档" }).click();
  await expect(page.getByRole("dialog")).toContainText("报销规则.md");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(state.deletes()).toBe(0);
  await page.getByRole("button", { name: "重建索引" }).click();
  await expect(page.getByText("当前仍使用已发布的有效构建。")).toBeVisible();
  expect(state.rebuilds()).toBe(1);
  await expect(page.getByRole("button", { name: "重建索引" })).toBeDisabled();
  await page.getByRole("button", { name: "删除文档" }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByRole("heading", { name: "还没有文档" })).toBeVisible();
  expect(state.deletes()).toBe(1);
});

test("pagination uses backend offsets, refresh recovers errors, narrow layout fits", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, { total: 11 });
  const request = page.waitForRequest((r) => r.url().includes("offset=10"));
  await page.getByRole("button", { name: "下一页" }).click();
  await request;
  await expect(page.getByText("第 2 / 2 页 · 共 11 份")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.route(`**${basePath}?**`, (route) => error(route));
  await page.getByRole("button", { name: "刷新文档" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.unroute(`**${basePath}?**`);
  await page.getByRole("button", { name: "刷新文档" }).click();
  await expect(
    page.getByRole("heading", { name: "报销规则.md", exact: true }),
  ).toBeVisible();
});

test("switch aborts an in-flight task request and ignores its late result", async ({
  page,
}) => {
  await page.clock.install();
  await setup(page, { docs: [doc("queued", job())] });
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let calls = 0;
  await page.route(`**${basePath}/${D}/jobs/${J}`, async (route) => {
    calls++;
    await held;
    await json(route, job("succeeded")).catch(() => {});
  });
  const pending = page.waitForRequest((request) =>
    request.url().endsWith(`/jobs/${J}`),
  );
  await page.clock.runFor(2100);
  await pending;
  const aborted = page.waitForEvent("requestfailed", (request) =>
    request.url().endsWith(`/jobs/${J}`),
  );
  await page.getByLabel("当前知识库", { exact: true }).selectOption(B);
  await aborted;
  release();
  await expect(page.getByRole("heading", { name: "还没有文档" })).toBeVisible();
  await page.clock.runFor(130000);
  expect(calls).toBe(1);
  await expect(page.getByText("最近任务：已完成")).toHaveCount(0);
});
