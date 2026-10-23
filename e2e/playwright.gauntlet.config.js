import { defineConfig } from 'playwright/test';

import { candidateOrigin, DEFAULT_CANDIDATE_URL } from './gauntlet/target.mjs';

const origin = candidateOrigin(process.env.BASE_URL || DEFAULT_CANDIDATE_URL);

export default defineConfig({
  testDir: './gauntlet',
  testMatch: '*.spec.js',
  timeout: 60_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: origin,
    browserName: 'chromium',
    serviceWorkers: 'block',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    userAgent: 'tho-candidate-gauntlet/1.0',
    ...(process.env.CHROMIUM_EXECUTABLE_PATH
      ? { launchOptions: { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH } }
      : {}),
  },
});
