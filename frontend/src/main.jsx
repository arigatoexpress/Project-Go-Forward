import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './App.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import { ToastProvider } from './components/Toast.jsx'
import { NetworkStatus } from './components/NetworkStatus.jsx'
import * as Sentry from '@sentry/react'

if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    environment: import.meta.env.MODE,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.05,
    beforeSend(event) {
      // Scrub chat message body to avoid leaking user input as PII.
      const body = event.request?.data
      if (body && typeof body === 'object' && 'message' in body) {
        body.message = '[REDACTED]'
      }
      return event
    },
  })

  // Wire existing ErrorBoundary instances (root + per-route) to Sentry.
  window.__SENTRY_HOOK__ = (error, errorInfo, { errorId }) => {
    Sentry.withScope((scope) => {
      scope.setTag('error_id', errorId)
      scope.setExtra('componentStack', errorInfo?.componentStack)
      Sentry.captureException(error)
    })
  }
}

// Inner tree reused as Sentry.ErrorBoundary fallback — if the main render
// fails catastrophically, a fresh instance of ErrorBoundary will catch and
// display the branded error UI.
const rootTree = (
  <ErrorBoundary>
    <ToastProvider>
      <NetworkStatus />
      <App />
    </ToastProvider>
  </ErrorBoundary>
)

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {import.meta.env.VITE_SENTRY_DSN ? (
      <Sentry.ErrorBoundary fallback={rootTree}>
        {rootTree}
      </Sentry.ErrorBoundary>
    ) : rootTree}
  </StrictMode>,
)
