import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Vitest configuration for the THO frontend smoke test harness.
// Kept separate from vite.config.js to avoid coupling test config to the
// production build. See docs/FRONTEND_TESTING_PROPOSAL.md for the full
// rationale (PR #23).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
    include: ['src/**/*.test.{js,jsx}'],
  },
});
