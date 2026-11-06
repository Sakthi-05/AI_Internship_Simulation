import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "module1-registration.spec.js",
  timeout: 360_000,
  expect: { timeout: 30_000 },
  reporter: "list",
  outputDir: "../.playwright-output-module1",
  use: {
    baseURL: process.env.M1_BASE_URL || "http://localhost:5173",
    browserName: "chromium",
    launchOptions: { channel: "msedge" },
    headless: true,
    screenshot: "off",
    trace: "off",
    video: "off",
  },
});
