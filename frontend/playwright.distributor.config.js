import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', testMatch: '*.spec.js',
  outputDir: '../test-results/distributor-integration', workers: 2,
  use: { baseURL: 'http://127.0.0.1:4174', channel: 'msedge', viewport: { width: 1440, height: 1000 } },
  webServer: { command: 'node test-static-server.cjs', url: 'http://127.0.0.1:4174', reuseExistingServer: false },
});
