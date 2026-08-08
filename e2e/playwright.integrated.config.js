import { defineConfig, devices } from "@playwright/test";

const apiBase = "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./integrated",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  // This journey mutates one real PostgreSQL order through payment, provider
  // callbacks, fulfillment, delivery, return and refund. Retrying against the
  // same database would create false secondary failures or mask the first failure,
  // so exact-run evidence is deliberately single-attempt.
  retries: 0,
  workers: 1,
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: "playwright-integrated-report", open: "never" }]]
    : "list",
  use: {
    ...devices["iPhone 13"],
    browserName: "chromium",
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command: "python -m uvicorn scripts.integrated_e2e_app:app --host 127.0.0.1 --port 8000",
      cwd: "..",
      url: `${apiBase}/ready`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        INTEGRATED_E2E: "true",
        MOYSKLAD_ORDER_EXPORT_ENABLED: "true",
      },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173",
      cwd: "../frontend",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { ...process.env, VITE_API_BASE: apiBase },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5174",
      cwd: "../admin",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { ...process.env, VITE_API_BASE: apiBase },
    },
  ],
});
