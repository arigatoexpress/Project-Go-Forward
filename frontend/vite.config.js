import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'
import { sentryVitePlugin } from '@sentry/vite-plugin'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Upload source maps to Sentry during CI builds.
    // Requires SENTRY_AUTH_TOKEN + SENTRY_ORG + SENTRY_PROJECT env vars.
    ...(process.env.SENTRY_AUTH_TOKEN
      ? [sentryVitePlugin({
          org: process.env.SENTRY_ORG,
          project: process.env.SENTRY_PROJECT,
        })]
      : []),
  ],
  build: {
    // Source maps are only generated when uploading to Sentry (saves bundle size otherwise).
    sourcemap: !!process.env.SENTRY_AUTH_TOKEN,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        studio: resolve(__dirname, 'studio.html'),
      },
      output: {
        manualChunks(id) {
          // Stable React runtime — cached independently across deploys.
          // React 19 exposes its runtime via react-dom/client (not bare react-dom),
          // so match on the node_modules path rather than the package specifier.
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor-react';
          }
          // Heavy charting library used only by Analytics page
          if (id.includes('node_modules/recharts') || id.includes('node_modules/d3-') || id.includes('node_modules/victory-vendor')) {
            return 'vendor-recharts';
          }
          // Markdown renderer used by SafeMarkdown (eagerly imported in main)
          if (id.includes('node_modules/react-markdown') || id.includes('node_modules/remark') || id.includes('node_modules/rehype') || id.includes('node_modules/unified') || id.includes('node_modules/micromark') || id.includes('node_modules/mdast') || id.includes('node_modules/hast') || id.includes('node_modules/vfile')) {
            return 'vendor-markdown';
          }
          // Icon library — large but tree-shaken; keep separate for cache stability
          if (id.includes('node_modules/lucide-react')) {
            return 'vendor-lucide';
          }
          // Sentry SDK — isolated so it doesn't inflate the main bundle
          if (id.includes('node_modules/@sentry/')) {
            return 'vendor-sentry';
          }
        },
      },
    },
  },
})
