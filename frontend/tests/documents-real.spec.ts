/** Only via test_frontend_documents.py: real disposable DB, API and single fake worker. */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
const kb = process.env.E2E_KB!;
async function open(page: Page, name = "alice") {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(`password-for-${name}`);
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await page.getByRole("button", { name: /LIBRARY \/ 01 A/ }).click();
  await page.getByRole("button", { name: "打开文档" }).click();
}

test("real upload, single worker, preview, rebuild, member restrictions and delete", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  await open(page);
  await expect(page.getByRole("heading", { name: "还没有文档" })).toBeVisible();
  await page.getByLabel("选择文件").setInputFiles({
    name: "演示报销.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# 演示数据\n\n## 报销规则\n\n产品 RD-101 报销上限为 680 元，须在 7 天内提交。",
    ),
  });
  const accepted = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith(`/knowledge-bases/${kb}/documents`),
  );
  await page.getByRole("button", { name: "上传文件" }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  const { document_id: id } = await response.json();
  await expect(page.getByText("最近任务：已完成")).toBeVisible({
    timeout: 12000,
  });
  await page.getByRole("button", { name: "切块预览" }).click();
  await expect(page.locator("pre")).toContainText("680 元");
  await expect(page.locator("pre")).toContainText("RD-101");
  const original = await page
    .locator(".preview-panel .document-id")
    .textContent();
  await page.screenshot({
    path: "../artifacts/validation/step26b/documents.png",
    fullPage: true,
  });
  const rebuilt = page.waitForResponse((r) => r.url().endsWith("/rebuild"));
  await page.getByRole("button", { name: "重建索引" }).click();
  expect((await rebuilt).status()).toBe(202);
  await expect(page.locator(".preview-panel .document-id")).not.toHaveText(
    original!,
    { timeout: 12000 },
  );
  await expect(page.getByText("最近任务：已完成")).toBeVisible();

  await page.getByRole("button", { name: "退出登录" }).click();
  await open(page, "carol");
  await expect(page.getByRole("button", { name: "上传文件" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "删除文档" })).toHaveCount(0);
  await page.getByRole("button", { name: "切块预览" }).click();
  await expect(page.locator("pre")).toContainText("680 元");
  const login = await request.post("/api/auth/session", {
    data: { login_name: "carol", password: "password-for-carol" },
  });
  expect(login.status()).toBe(200);
  const { access_token: token } = await login.json();
  const headers = { Authorization: `Bearer ${token}` };
  const path = `/api/knowledge-bases/${kb}/documents/${id}`;
  expect((await request.delete(path, { headers })).status()).toBe(403);
  expect((await request.post(path + "/rebuild", { headers })).status()).toBe(
    403,
  );
  expect(
    (
      await request.post(`/api/knowledge-bases/${kb}/documents`, {
        headers,
        multipart: {
          file: {
            name: "no.txt",
            mimeType: "text/plain",
            buffer: Buffer.from("no"),
          },
        },
      })
    ).status(),
  ).toBe(403);
  expect(
    (
      await request.get(
        path.replace(kb, process.env.E2E_FOREIGN_KB!) + "/preview",
        { headers },
      )
    ).status(),
  ).toBe(404);

  await page.getByRole("button", { name: "退出登录" }).click();
  await open(page);
  await page.getByRole("button", { name: "删除文档" }).click();
  await expect(page.getByRole("dialog")).toContainText("演示报销.md");
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByRole("heading", { name: "还没有文档" })).toBeVisible();
  expect((await request.get(path + "/preview", { headers })).status()).toBe(
    404,
  );
});
