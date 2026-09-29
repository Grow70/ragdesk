import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  D = "dddddddd-dddd-dddd-dddd-dddddddddddd",
  C = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const source = {
  document_id: D,
  build_id: B,
  chunk_id: C,
  document_name: "演示报销.md",
  snippet: "演示数据：限额 680 元，7 天内提交。",
  page_number: 2,
  heading_path: ["差旅报销"],
  start_line: null,
  end_line: null,
};
const citation = {
  ...source,
  citation_id: "c1",
  source_path: "https://untrusted.invalid/never-request",
};
const summary = (mode = "rag") => ({
  mode,
  events:
    mode === "agent"
      ? [
          {
            step: 1,
            tool: "search_knowledge",
            status: "success",
            query_summary: "报销期限",
            result_count: 1,
          },
        ]
      : [],
  termination_reason: mode === "agent" ? "model_finished" : null,
  tool_call_count: mode === "agent" ? 1 : null,
  model_call_count: mode === "agent" ? 4 : null,
});
const result = (mode = "rag") => ({
  ...summary(mode),
  status: "answered",
  answer: "## 报销要求\n\n上限 **680 元**，7 天内提交。[c1]",
  citations: [citation],
  request_id: "answer-request",
});
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
const failure = (route: Route, status: number, code = "ERROR", extra = {}) =>
  json(
    route,
    {
      error: { code, message: "private provider body" },
      request_id: "error-request",
      ...extra,
    },
    status,
  );
const answers = `**/api/knowledge-bases/${A}/answers`,
  sources = `**/api/knowledge-bases/${A}/sources/**`;
async function setup(page: Page) {
  const requests: unknown[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/session"))
      return json(route, {
        access_token: "memory-test-token",
        token_type: "bearer",
        expires_in: 1800,
      });
    expect(route.request().headers().authorization).toBe(
      "Bearer memory-test-token",
    );
    if (path.endsWith("/auth/me"))
      return json(route, {
        user_id: A,
        login_name: "alice",
        display_name: "演示成员",
        request_id: "me",
      });
    const bases = [A, B].map((id, i) => ({
      id,
      name: i ? "产品资料" : "团队制度",
      role: "member",
      created_at: "2026-09-29T00:00:00Z",
      request_id: "kb",
    }));
    if (path === "/api/knowledge-bases") return json(route, { items: bases });
    const kb = bases.find((b) => path === `/api/knowledge-bases/${b.id}`);
    if (kb) return json(route, kb);
    if (path.endsWith("/answers")) {
      const body = route.request().postDataJSON();
      requests.push(body);
      return json(route, result(body.mode));
    }
    if (path === `/api/knowledge-bases/${A}/sources/${D}/${B}/${C}`)
      return json(route, { ...source, request_id: "source" });
    return failure(route, 404);
  });
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("alice");
  await page.getByLabel("密码", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await page.getByRole("button", { name: /团队制度/ }).click();
  await page.getByRole("button", { name: "开始问答" }).click();
  return requests;
}
async function ask(page: Page, question = "报销要求是什么？") {
  await page.getByLabel("你的问题").fill(question);
  await page.getByRole("button", { name: "提交问题" }).click();
}

test("fixed answer renders Markdown and authorized source; each question is independent", async ({
  page,
}) => {
  const requests = await setup(page);
  await ask(page);
  await expect(
    page.getByRole("heading", { name: "报销要求", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".answer-markdown strong")).toHaveText("680 元");
  await page.getByRole("button", { name: /\[c1\]/ }).click();
  const panel = page.getByRole("region", { name: "引用原文" });
  await expect(panel).toContainText("第 2 页 · 差旅报销");
  await expect(panel.locator("pre")).toHaveText(source.snippet);
  await ask(page, "另一个独立问题");
  await expect(panel).toHaveCount(0);
  await expect(page.getByText("另一个独立问题", { exact: true })).toBeVisible();
  expect(requests).toEqual([
    { question: "报销要求是什么？", mode: "rag" },
    { question: "另一个独立问题", mode: "rag" },
  ]);
});

test("Agent mode shows only returned tool events after completion", async ({
  page,
}) => {
  const requests = await setup(page);
  await page.getByRole("radio", { name: /Agent/ }).check();
  await ask(page);
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toContainText("搜索知识库");
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toContainText("成功 · 1 条结果");
  expect(requests).toEqual([{ question: "报销要求是什么？", mode: "agent" }]);
});

for (const status of ["insufficient_evidence", "needs_clarification"])
  test(`shows ${status} without invented sources`, async ({ page }) => {
    await setup(page);
    await page.route(answers, (r) =>
      json(r, {
        ...result(),
        status,
        answer:
          status === "needs_clarification"
            ? "请补充适用产品。"
            : "当前资料不足。",
        citations: [],
      }),
    );
    await ask(page);
    await expect(
      page.getByText(
        status === "needs_clarification" ? "需要补充信息" : "资料不足",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /\[c1\]/ })).toHaveCount(0);
  });

test("Markdown drops HTML and never fetches model URLs", async ({ page }) => {
  await setup(page);
  let outbound = 0;
  await page.route("https://untrusted.invalid/**", (r) => {
    outbound++;
    return r.abort();
  });
  await page.route(answers, (r) =>
    json(r, {
      ...result(),
      answer:
        '<script>window.pwned=true</script>\n\n<img src="https://untrusted.invalid/pixel" onerror="window.pwned=true">\n\n[危险](javascript:alert(1)) ![图片](https://untrusted.invalid/image) [外链](https://untrusted.invalid/link)\n\n**安全文字** [c1]',
    }),
  );
  await ask(page);
  await expect(page.locator(".answer-markdown strong")).toHaveText("安全文字");
  expect(await page.evaluate(() => "pwned" in window)).toBe(false);
  expect(outbound).toBe(0);
  await expect(
    page.locator(
      ".answer-markdown script,.answer-markdown img,.answer-markdown a",
    ),
  ).toHaveCount(0);
});

for (const status of [404, 410])
  test(`source ${status} removes old snippet`, async ({ page }) => {
    await setup(page);
    await ask(page);
    await page.getByRole("button", { name: /\[c1\]/ }).click();
    await expect(page.locator(".source-panel pre")).toBeVisible();
    await page.route(sources, (r) => failure(r, status));
    await page.getByRole("button", { name: /\[c1\]/ }).click();
    await expect(page.getByRole("alert")).toContainText(
      status === 410 ? "旧构建已失效" : "不可访问",
    );
    await expect(page.locator(".source-panel pre")).toHaveCount(0);
  });

test("server timeout preserves safe Agent summary; validation failure is not an answer", async ({
  page,
}) => {
  await setup(page);
  await page.getByRole("radio", { name: /Agent/ }).check();
  await page.route(answers, (r) =>
    failure(r, 504, "AGENT_DEADLINE_TIMEOUT", {
      ...summary("agent"),
      termination_reason: "deadline",
    }),
  );
  await ask(page);
  await expect(page.getByRole("alert")).toContainText("超时");
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toContainText("本轮超时");
  await expect(page.getByRole("region", { name: "问答结果" })).toHaveCount(0);
  await page.route(answers, (r) => failure(r, 502, "ANSWER_INVALID_CITATIONS"));
  await ask(page);
  await expect(page.getByRole("alert")).toContainText("未通过后端校验");
});

test("browser timeout is 75 seconds and never retries paid requests", async ({
  page,
}) => {
  await page.clock.install();
  await setup(page);
  let calls = 0;
  await page.route(answers, () => {
    calls++;
  });
  await ask(page);
  await expect(page.getByText(/正在生成并校验回答/)).toBeVisible();
  await page.clock.runFor(16000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.clock.fastForward(60000);
  await expect(page.getByRole("alert")).toContainText("超时");
  expect(calls).toBe(1);
});

test("switching KB clears question, result and source; late answer cannot restore old scope", async ({
  page,
}) => {
  await setup(page);
  await ask(page);
  await page.getByRole("button", { name: /\[c1\]/ }).click();
  await expect(page.locator(".source-panel pre")).toBeVisible();
  let release: () => void = () => {};
  const hold = new Promise<void>((r) => {
    release = r;
  });
  await page.route(answers, async (r) => {
    await hold;
    await json(r, result()).catch(() => {});
  });
  const sent = page.waitForRequest((r) => r.url().endsWith("/answers"));
  await ask(page, "待取消的问题");
  await sent;
  await page.getByLabel("问答知识库").selectOption(B);
  release();
  await expect(page.getByLabel("你的问题")).toHaveValue("");
  await expect(page.locator(".answer-panel,.source-panel")).toHaveCount(0);
});

test("mode change cancels the current answer and clears source", async ({
  page,
}) => {
  await setup(page);
  await ask(page);
  await page.getByRole("button", { name: /\[c1\]/ }).click();
  await expect(page.locator(".source-panel pre")).toBeVisible();
  await page.getByRole("radio", { name: /Agent/ }).check();
  await expect(page.locator(".answer-panel,.source-panel")).toHaveCount(0);
  await ask(page);
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toBeVisible();
});

test("empty input, no-permission error and expired session have clear states", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.getByRole("button", { name: "提交问题" }).click();
  await expect(page.getByRole("alert")).toContainText("1～4000");
  await page.getByLabel("你的问题").fill("问".repeat(4001));
  await expect(page.getByLabel("你的问题")).toHaveValue("问".repeat(4000));
  await page.route(answers, (r) => failure(r, 404));
  await ask(page);
  await expect(page.getByRole("alert")).toContainText("权限已失效");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.route(answers, (r) => failure(r, 401));
  await ask(page);
  await expect(
    page.getByRole("heading", { name: "登录 Ragdesk" }),
  ).toBeVisible();
});
