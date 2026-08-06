import { defineConfig, devices } from "@playwright/test";

const browserChannel =
  process.env.PLAYWRIGHT_BROWSER_CHANNEL === "chrome" || process.env.PLAYWRIGHT_BROWSER_CHANNEL === "msedge"
    ? process.env.PLAYWRIGHT_BROWSER_CHANNEL
    : undefined;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], ...(browserChannel ? { channel: browserChannel } : {}) },
    },
  ],
  outputDir: "test-results/playwright",
});
