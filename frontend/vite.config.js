import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { resolve } from 'path'
import { sentryVitePlugin } from '@sentry/vite-plugin'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      // Registration is non-critical; keep it out of the render path.
      injectRegister: 'script-defer',

      manifest: {
        name: 'Texas Home Outlet',
        short_name: 'THO',
        description: 'Texas Home Outlet - AI-Powered Manufactured Home Sales & Service',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#1e3a5f',
        theme_color: '#1e3a5f',
        icons: [
          {
            src: '/tex-icon.svg',
            sizes: 'any',
            type: 'image/svg+xml',
            purpose: 'any',
          },
          {
            src: '/tex-icon.svg',
            sizes: 'any',
            type: 'image/svg+xml',
            purpose: 'maskable',
          },
        ],
      },

      workbox: {
        skipWaiting: true,
        clientsClaim: true,

        // Never precache HTML — must stay network-fetched per Cache-Control policy.
        // Never precache admin chunks — they're behind PIN auth and lazy-loaded.
        globIgnores: [
          '**/index.html',
          '**/studio.html',
          '**/CRM-*.{js,css}',
          '**/Analytics-*.{js,css}',
          '**/ChatHistory-*.{js,css}',
          '**/DocumentCenter-*.{js,css}',
          '**/AdStudio-*.{js,css}',
          '**/studio-*.js',
          '**/downloadAdminFile-*.js',
          // recharts (~392 KB / 114 KB gz) is used only by the admin Analytics +
          // CRM pages (both lazy-loaded), so public buyers never need it. Keep it
          // out of the precache — admins still fetch it from network at runtime.
          '**/vendor-recharts-*.js',
        ],

        // No navigation fallback: uncached navigations go to network, giving a
        // clean browser error offline rather than a blank page.
        navigateFallback: null,

        // Belt-and-suspenders: never serve cached responses for admin routes.
        navigateFallbackDenylist: [
          /^\/documents/,
          /^\/crm/,
          /^\/studio/,
          /^\/analytics/,
          /^\/chat-history/,
        ],

        runtimeCaching: [
          // Inventory context powers the customer Inventory page — cache 24 h,
          // network-first so fresh data wins whenever online.
          {
            urlPattern: ({ url }) => url.pathname === '/api/marketing/inventory-context',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'inventory-api-cache',
              expiration: {
                maxEntries: 1,
                maxAgeSeconds: 86400, // 24 h
              },
              networkTimeoutSeconds: 10,
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          // All other API routes: never cache.
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/api/'),
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
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
