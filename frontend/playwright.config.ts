import { defineConfig, devices } from "@playwright/test";

// Local test servers must bypass an inherited corporate/system HTTP proxy.
process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;

const real = process.env.E2E_REAL === "1";
export default defineConfig({
  testDir: "./tests",
  testMatch: real
    ? process.env.E2E_DOCUMENTS === "1"
      ? "documents-real.spec.ts"
      : "real.spec.ts"
    : ["auth.spec.ts", "documents.spec.ts"],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20_000,
  reporter: "list",
  outputDir: "test-results",
  // No traces/video: they can retain passwords and Authorization headers.
  use: {
    baseURL: "http://127.0.0.1:5173",
    ...devices["Desktop Chrome"],
    channel: "chromium",
    trace: "off",
    screenshot: "off",
  },
  webServer: {
    command: "npm run dev",
    // Avoid a pre-start HTTP probe of a closed WSL-forwarded port.
    // Vite strictPort still refuses to reuse someone else's server.
    wait: { stdout: /http:\/\/127\.0\.0\.1:5173/ },
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
