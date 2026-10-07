import { defineConfig } from 'playwright/test';

const baseURL = process.env.BASE_URL || 'https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app';
const target = new URL(baseURL);
if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) {
  throw new Error('BASE_URL must be an HTTP(S) URL without credentials');
}

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: target.origin,
    browserName: 'chromium',
    serviceWorkers: 'block',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    ...(process.env.CHROMIUM_EXECUTABLE_PATH ? {
      launchOptions: { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH },
    } : {}),
  },
});
