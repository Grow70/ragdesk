/** Real browser/API/worker/pgvector. No route interception or model network. */
import { test, expect } from "@playwright/test";

const kb = process.env.E2E_KB!;
const other = process.env.E2E_FOREIGN_KB!;

test("login → upload → ingest → answer → source; permissions and deletion", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("alice");
  await page.getByLabel("密码", { exact: true }).fill("password-for-alice");
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await page.getByRole("button", { name: /LIBRARY \/ 01 A/ }).click();
  await page.getByRole("button", { name: "打开文档" }).click();
  await page.getByLabel("选择文件").setInputFiles({
    name: "演示规则.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 演示数据\n\nRD-101 报销上限 680 元，7 天内提交。"),
  });
  const accepted = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith(`/knowledge-bases/${kb}/documents`),
  );
  await page.getByRole("button", { name: "上传文件" }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  const upload = await response.json();
  await expect(page.getByText("最近任务：已完成")).toBeVisible({
    timeout: 15000,
  });
  // Return to the library and ask about the exact document just uploaded.
  await page.getByRole("button", { name: "← 返回知识库", exact: true }).click();
  await page.getByRole("button", { name: "开始问答" }).click();
  await page.getByLabel("你的问题").fill("RD-101 报销上限是多少？");
  const answered = page.waitForResponse((r) =>
    r.url().endsWith(`/knowledge-bases/${kb}/answers`),
  );
  await page.getByRole("button", { name: "提交问题" }).click();
  const answerResponse = await answered;
  expect(answerResponse.status()).toBe(200);
  const answer = await answerResponse.json();
  expect(answer.status).toBe("answered");
  expect(answer.citations[0].document_id).toBe(upload.document_id);
  await expect(page.locator(".answer-markdown")).toContainText("FAKE");
  await page.getByRole("button", { name: /\[c1\]/ }).click();
  await expect(page.locator(".source-panel pre")).toContainText("680 元");
  await expect(page.locator(".source-panel")).toContainText("演示规则.md");
  await page.getByRole("radio", { name: /Agent/ }).check();
  await page.getByRole("button", { name: "提交问题" }).click();
  await expect(page.getByText("已回答", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toContainText("成功 · 1 条结果");

  async function login(name: string) {
    const r = await request.post("/api/auth/session", {
      data: { login_name: name, password: `password-for-${name}` },
    });
    expect(r.status()).toBe(200);
    return { Authorization: `Bearer ${(await r.json()).access_token}` };
  }
  const admin = await login("alice"),
    member = await login("carol"),
    outsider = await login("bob");
  const base = `/api/knowledge-bases/${kb}`;
  const doc = `${base}/documents/${upload.document_id}`;
  const source = `/api${answer.citations[0].source_path}`;
  const job = `/api${upload.status_url}`;
  expect((await request.get(source, { headers: member })).status()).toBe(200);
  expect((await request.delete(doc, { headers: member })).status()).toBe(403);
  expect(
    (await request.post(doc + "/rebuild", { headers: member })).status(),
  ).toBe(403);
  expect(
    (
      await request.post(base + "/documents", {
        headers: member,
        multipart: {
          file: {
            name: "forbidden.txt",
            mimeType: "text/plain",
            buffer: Buffer.from("演示数据"),
          },
        },
      })
    ).status(),
  ).toBe(403);
  expect((await request.get(job, { headers: member })).status()).toBe(403);
  // B admin cannot access A, nor transplant A object IDs into their own B path.
  for (const target of [kb, other]) {
    for (const path of [source, doc + "/raw", job]) {
      const denied = await request.get(path.replace(kb, target), {
        headers: outsider,
      });
      expect(denied.status()).toBe(404);
      expect(await denied.text()).not.toContain("680");
    }
  }
  expect(
    (
      await request.post(base + "/search", {
        headers: outsider,
        data: { query: "680", top_k: 5 },
      })
    ).status(),
  ).toBe(404);
  const ownEmpty = await request.post(`/api/knowledge-bases/${other}/search`, {
    headers: outsider,
    data: { query: "680" },
  });
  expect(ownEmpty.status()).toBe(200);
  expect((await ownEmpty.json()).items).toEqual([]);
  const noEvidence = await request.post(
    `/api/knowledge-bases/${other}/answers`,
    { headers: outsider, data: { question: "报销规则是什么？" } },
  );
  expect(noEvidence.status()).toBe(200);
  expect((await noEvidence.json()).status).toBe("insufficient_evidence");
  expect((await request.delete(doc, { headers: admin })).status()).toBe(200);
  const search = await request.post(base + "/search", {
    headers: member,
    data: { query: "RD-101 680" },
  });
  expect(search.status()).toBe(200);
  expect((await search.json()).items).toEqual([]);
  expect((await request.get(source, { headers: member })).status()).toBe(404);
  expect((await request.get(doc + "/raw", { headers: member })).status()).toBe(
    404,
  );
  for (const mode of ["rag", "agent"]) {
    const refused = await request.post(base + "/answers", {
      headers: member,
      data: { question: "报销上限？", mode },
    });
    expect(refused.status()).toBe(200);
    const body = await refused.json();
    expect(body.status).toBe("insufficient_evidence");
    expect(body.citations).toEqual([]);
  }
});
