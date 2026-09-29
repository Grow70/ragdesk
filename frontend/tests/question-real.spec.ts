/** Only via test_frontend_question.py: real HTTP/DB/auth/loop, controlled models. */
import { test, expect } from "@playwright/test";

test("real fixed and Agent answers, authorized source, empty library and isolation", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("alice");
  await page.getByLabel("密码", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await page.getByRole("button", { name: /A · 演示报销/ }).click();
  await page.getByRole("button", { name: "开始问答" }).click();
  await page.getByLabel("你的问题").fill("报销要求是什么？");
  await page.getByRole("button", { name: "提交问题" }).click();
  await expect(page.getByText("已回答", { exact: true })).toBeVisible();
  await expect(page.locator(".answer-markdown strong")).toHaveText("680 元");
  await page.getByRole("button", { name: /\[c1\]/ }).click();
  await expect(page.locator(".source-panel pre")).toContainText("7 天内提交");
  await expect(page.locator(".source-panel")).toContainText("第 1 页");
  await page.getByRole("radio", { name: /Agent/ }).check();
  await expect(page.locator(".source-panel")).toHaveCount(0);
  await page.getByRole("button", { name: "提交问题" }).click();
  await expect(page.getByText("已回答", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toContainText("成功 · 1 条结果");
  await expect(
    page.getByRole("region", { name: "工具执行事件" }),
  ).toContainText("模型请求 4 次");
  await page.getByRole("button", { name: /\[c1\]/ }).click();
  await expect(page.locator(".source-panel pre")).toContainText("680 元");
  await page.screenshot({
    path: "../artifacts/validation/step27/question.png",
    fullPage: true,
  });
  await page.getByLabel("问答知识库").selectOption(process.env.E2E_EMPTY_KB!);
  await expect(page.getByLabel("你的问题")).toHaveValue("");
  await expect(page.locator(".answer-panel,.source-panel")).toHaveCount(0);
  await page.getByLabel("你的问题").fill("未知制度是什么？");
  await page.getByRole("button", { name: "提交问题" }).click();
  await expect(page.getByText("资料不足", { exact: true })).toBeVisible();
  const login = await request.post("/api/auth/session", {
    data: { login_name: "alice", password: "test-password" },
  });
  expect(login.status()).toBe(200);
  const { access_token } = await login.json();
  const forbidden = await request.post(
    `/api/knowledge-bases/${process.env.E2E_FOREIGN_KB}/answers`,
    {
      headers: { Authorization: `Bearer ${access_token}` },
      data: { question: "不属于我的资料", mode: "agent" },
    },
  );
  expect(forbidden.status()).toBe(404);
});
