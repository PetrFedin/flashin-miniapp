import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : "list",
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "storefront-mobile",
      testMatch: /(?:storefront|.*-storefront)\.spec\.js/,
      testIgnore: /responsive-storefront\.spec\.js/,
      use: {
        ...devices["iPhone 13"],
        browserName: "chromium",
        baseURL: "http://127.0.0.1:5173",
      },
    },
    {
      name: "storefront-tablet",
      testMatch: /responsive-storefront\.spec\.js/,
      use: {
        browserName: "chromium",
        baseURL: "http://127.0.0.1:5173",
        viewport: { width: 834, height: 1194 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "storefront-wide",
      testMatch: /responsive-storefront\.spec\.js/,
      use: {
        browserName: "chromium",
        baseURL: "http://127.0.0.1:5173",
        viewport: { width: 1440, height: 900 },
        hasTouch: false,
        isMobile: false,
      },
    },
    {
      name: "admin-desktop",
      testMatch: /(?:admin|admin-.*|.*-admin)\.spec\.js/,
      testIgnore: /responsive-admin\.spec\.js/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: "http://127.0.0.1:5174",
      },
    },
    {
      name: "admin-tablet",
      testMatch: /responsive-admin\.spec\.js/,
      use: {
        browserName: "chromium",
        baseURL: "http://127.0.0.1:5174",
        viewport: { width: 834, height: 1194 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
  webServer: [
    {
      command: "npm install --no-audit --no-fund && npm run dev",
      cwd: "../frontend",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm install --no-audit --no-fund && npm run dev",
      cwd: "../admin",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
