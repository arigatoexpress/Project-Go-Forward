/**
 * Error Boundary Component
 *
 * Catches JavaScript errors in child components and displays a branded
 * fallback UI instead of cascading a render error to a white screen.
 *
 * Used at two levels:
 *   1. Root (in main.jsx) — last-resort backstop around the whole app.
 *   2. Per-route (in App.jsx) — wraps each Suspense-loaded page so a
 *      crash in one page (e.g. CRM) does not blank the inventory tab,
 *      navigation, or footer.
 *
 * Observability hook:
 *   componentDidCatch invokes window.__SENTRY_HOOK__ if defined. This
 *   lets us add a real @sentry/react SDK later (separate PR) without
 *   touching this component.
 */

import React from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

// Short, human-readable id so a user can quote it to support.
// Not cryptographic — just a correlation handle.
function makeErrorId() {
  const rand = Math.random().toString(36).slice(2, 8).toUpperCase();
  const ts = Date.now().toString(36).slice(-4).toUpperCase();
  return `ERR-${ts}-${rand}`;
}

// Best-effort, fire-and-forget crash report to the dedicated /api/analytics
// sink. Self-contained (no page-chunk imports) so the root backstop never
// depends on a module that may itself have failed to load. Wrapped so a
// reporting failure can NEVER take the app down — this is observability only.
function reportCrash({ errorId, error, errorInfo, scope }) {
  try {
    const payload = JSON.stringify({
      event: 'react_error_boundary',
      errorId,
      scope: scope || undefined,
      message: error?.toString?.() ?? 'unknown',
      componentStack: errorInfo?.componentStack ?? undefined,
      path: typeof window !== 'undefined' ? window.location.pathname : undefined,
    });
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      navigator.sendBeacon('/api/analytics', new Blob([payload], { type: 'application/json' }));
    } else if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
      window.fetch('/api/analytics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    }
  } catch {
    // Crash reporting must never interrupt the fallback UI.
  }
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      errorId: null,
    };
  }

  static getDerivedStateFromError(error) {
    // First pass on error — flip the fallback flag and stash the error.
    // errorInfo + errorId arrive in componentDidCatch.
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    const errorId = makeErrorId();
    this.setState({ errorInfo, errorId });

    // Always log to console so it's visible in DevTools / Cloud Run logs.
    console.error(`[ErrorBoundary ${errorId}]`, error, errorInfo);

    // Best-effort crash report to /api/analytics. Self-wrapped; never throws.
    reportCrash({ errorId, error, errorInfo, scope: this.props.scope });

    // Call the global Sentry hook if observability layer is attached.
    if (typeof window !== 'undefined' && typeof window.__SENTRY_HOOK__ === 'function') {
      try {
        window.__SENTRY_HOOK__(error, errorInfo, { errorId, scope: this.props.scope });
      } catch (hookErr) {
        // Never let the error reporter take the app down.
        console.error('[ErrorBoundary] sentry hook threw', hookErr);
      }
    }

    // Legacy gtag passthrough (kept for existing analytics wiring).
    if (typeof window !== 'undefined' && typeof window.gtag === 'function') {
      window.gtag('event', 'exception', {
        description: `${error?.toString?.() ?? 'unknown'} ${errorInfo?.componentStack ?? ''}`,
        fatal: true,
      });
    }
  }

  handleReload = () => {
    if (typeof window !== 'undefined') {
      window.location.reload();
    }
  };

  handleRetry = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      errorId: null,
    });
  };

  render() {
    if (this.state.hasError) {
      const { errorId, error, errorInfo } = this.state;
      const scopeLabel = this.props.scope ? ` in ${this.props.scope}` : '';

      return (
        <main id="main-content">
        <div
          role="alert"
          aria-live="assertive"
          className="flex items-center justify-center p-6"
          style={{
            minHeight: '60vh',
            background: 'var(--tho-bg, #f8fafc)',
          }}
        >
          <div
            className="w-full max-w-lg overflow-hidden"
            style={{
              background: 'var(--tho-bg-card, #ffffff)',
              border: '1px solid var(--tho-border, #e2e8f0)',
              borderRadius: 'var(--tho-radius-xl, 16px)',
              boxShadow: 'var(--tho-shadow-lg, 0 12px 28px rgba(0,0,0,0.12))',
            }}
          >
            {/* Header */}
            <div
              className="p-6 text-center"
              style={{ background: 'var(--tho-danger, #ef4444)' }}
            >
              <div
                className="w-14 h-14 mx-auto mb-3 flex items-center justify-center"
                style={{
                  background: 'rgba(255,255,255,0.18)',
                  borderRadius: 'var(--tho-radius-full, 9999px)',
                }}
              >
                <AlertTriangle size={28} className="text-white" />
              </div>
              <h2 className="text-xl font-bold text-white">
                Something went wrong on this page
              </h2>
              <p className="text-sm mt-1" style={{ color: 'rgba(255,255,255,0.92)' }}>
                You can keep using the rest of the app. Our team has been notified.
              </p>
            </div>

            {/* Body */}
            <div className="p-6 space-y-4">
              <p
                className="text-sm text-center"
                style={{ color: 'var(--tho-text-secondary, #475569)' }}
              >
                If you contact support, please share this reference id so we can
                find the matching log entry:
              </p>
              <div className="text-center">
                <code
                  className="inline-block px-3 py-1 text-sm font-mono"
                  style={{
                    background: 'var(--tho-border-light, #f1f5f9)',
                    color: 'var(--tho-text, #1e293b)',
                    borderRadius: 'var(--tho-radius-sm, 6px)',
                  }}
                >
                  {errorId || 'ERR-PENDING'}
                </code>
              </div>

              {/* Dev-only stack details */}
              {import.meta.env.DEV && error && (
                <details
                  className="text-xs"
                  style={{ color: 'var(--tho-text-muted, #64748b)' }}
                >
                  <summary className="cursor-pointer font-semibold">
                    Stack trace (dev only{scopeLabel})
                  </summary>
                  <pre
                    className="mt-2 p-3 overflow-auto whitespace-pre-wrap font-mono"
                    style={{
                      background: '#0f172a',
                      color: '#f8fafc',
                      borderRadius: 'var(--tho-radius-md, 8px)',
                      maxHeight: '12rem',
                    }}
                  >
                    {error.toString()}
                    {errorInfo?.componentStack || ''}
                  </pre>
                </details>
              )}

              {/* Actions */}
              <div className="flex flex-col sm:flex-row gap-3 pt-2">
                <button
                  type="button"
                  onClick={this.handleRetry}
                  className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 font-semibold text-white"
                  style={{
                    background: 'var(--tho-primary, #3b82f6)',
                    borderRadius: 'var(--tho-radius-lg, 12px)',
                    transition: 'background var(--tho-transition-fast, 0.15s ease)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'var(--tho-primary-dark, #2563eb)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'var(--tho-primary, #3b82f6)';
                  }}
                >
                  <RefreshCw size={16} />
                  Try again
                </button>
                <button
                  type="button"
                  onClick={this.handleReload}
                  className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 font-semibold"
                  style={{
                    background: 'var(--tho-bg-card, #ffffff)',
                    color: 'var(--tho-text, #1e293b)',
                    border: '1px solid var(--tho-border, #e2e8f0)',
                    borderRadius: 'var(--tho-radius-lg, 12px)',
                  }}
                >
                  <RefreshCw size={16} />
                  Reload page
                </button>
                <a
                  href="/"
                  className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 font-semibold no-underline"
                  style={{
                    background: 'var(--tho-bg-card, #ffffff)',
                    color: 'var(--tho-text, #1e293b)',
                    border: '1px solid var(--tho-border, #e2e8f0)',
                    borderRadius: 'var(--tho-radius-lg, 12px)',
                  }}
                >
                  <Home size={16} />
                  Go home
                </a>
              </div>

              <div
                className="pt-3 text-center text-xs"
                style={{
                  color: 'var(--tho-text-muted, #64748b)',
                  borderTop: '1px solid var(--tho-border-light, #f1f5f9)',
                }}
              >
                Need help? Email{' '}
                <a
                  href="mailto:support@texashomeoutlet.com"
                  style={{ color: 'var(--tho-primary, #3b82f6)' }}
                >
                  support@texashomeoutlet.com
                </a>
              </div>
            </div>
          </div>
        </div>
        </main>
      );
    }

    return <main id="main-content">{this.props.children}</main>;
  }
}

/**
 * Lightweight inline fallback for sub-component errors. Kept for any
 * existing call-sites — the main `ErrorBoundary` is the preferred wrap
 * for a routed page.
 */
export function ComponentErrorFallback({ _error, resetError }) {
  return (
    <div
      className="p-4"
      style={{
        background: 'var(--tho-danger-light, #fef2f2)',
        border: '1px solid var(--tho-danger, #ef4444)',
        borderRadius: 'var(--tho-radius-lg, 12px)',
        color: 'var(--tho-danger, #ef4444)',
      }}
    >
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle size={18} />
        <span className="font-semibold">Component error</span>
      </div>
      <p className="text-sm mb-3">
        This section encountered an error. You can try reloading it.
      </p>
      <button
        type="button"
        onClick={resetError}
        className="text-sm px-3 py-1.5 text-white font-semibold"
        style={{
          background: 'var(--tho-danger, #ef4444)',
          borderRadius: 'var(--tho-radius-md, 8px)',
        }}
      >
        Retry
      </button>
    </div>
  );
}

export default ErrorBoundary;
