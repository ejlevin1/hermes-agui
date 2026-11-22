import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./test", timeout: 90000, workers: 1, fullyParallel: false, reporter: [["list"], ["json", { outputFile: "results.json" }]],
  use: { headless: true },
});
