// The app is started and stopped by Playwright for each run, so `specanvil proof` needs no start command.
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './e2e',
  workers: 1,
  use: {
    baseURL: 'http://localhost:4173',
    channel: process.env.PW_CHANNEL || undefined, // e.g. PW_CHANNEL=chrome to use an installed Chrome
  },
  webServer: { command: 'node server.js', url: 'http://localhost:4173', reuseExistingServer: false },
});
