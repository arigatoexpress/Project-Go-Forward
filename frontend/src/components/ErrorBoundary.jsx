/**
 * Error Boundary Component
 * 
 * Catches JavaScript errors in child components and displays a fallback UI
 * instead of crashing the entire application.
 */

import React from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { 
      hasError: false, 
      error: null, 
      errorInfo: null,
      errorCount: 0
    };
  }

  static getDerivedStateFromError(_error) {
    // Update state so the next render will show the fallback UI
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // Log error details
    this.setState({
      error: error,
      errorInfo: errorInfo,
      errorCount: this.state.errorCount + 1
    });

    // Send to analytics/logging service
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    
    // Could send to Sentry, LogRocket, etc.
    if (window.gtag) {
      window.gtag('event', 'exception', {
        description: `${error.toString()} ${errorInfo.componentStack}`,
        fatal: true
      });
    }
  }

  handleReload = () => {
    window.location.reload();
  };

  handleGoHome = () => {
    window.location.href = '/';
  };

  handleRetry = () => {
    this.setState({ 
      hasError: false, 
      error: null, 
      errorInfo: null 
    });
  };

  render() {
    if (this.state.hasError) {
      // Error UI
      return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
          <div className="max-w-lg w-full bg-white rounded-2xl shadow-xl overflow-hidden">
            {/* Header */}
            <div className="bg-red-500 p-6 text-center">
              <div className="w-16 h-16 bg-white/20 rounded-full flex items-center justify-center mx-auto mb-4">
                <AlertTriangle size={32} className="text-white" />
              </div>
              <h1 className="text-2xl font-bold text-white">Something Went Wrong</h1>
              <p className="text-red-100 mt-2">
                We apologize for the inconvenience. Our team has been notified.
              </p>
            </div>

            {/* Error Details (in development) */}
            {import.meta.env.DEV && this.state.error && (
              <div className="p-4 bg-gray-100 border-b">
                <details className="text-sm">
                  <summary className="font-semibold text-gray-700 cursor-pointer">
                    Error Details (Development Only)
                  </summary>
                  <div className="mt-2 p-3 bg-gray-800 text-gray-100 rounded-lg overflow-auto max-h-48 font-mono text-xs">
                    <p className="text-red-400 font-semibold">{this.state.error.toString()}</p>
                    {this.state.errorInfo && (
                      <pre className="mt-2 whitespace-pre-wrap">
                        {this.state.errorInfo.componentStack}
                      </pre>
                    )}
                  </div>
                </details>
              </div>
            )}

            {/* Actions */}
            <div className="p-6 space-y-3">
              <button
                onClick={this.handleRetry}
                className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-blue-600 text-white rounded-xl font-semibold hover:bg-blue-700 transition-colors"
              >
                <RefreshCw size={20} />
                Try Again
              </button>

              <div className="flex gap-3">
                <button
                  onClick={this.handleReload}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-3 border-2 border-gray-200 text-gray-700 rounded-xl font-semibold hover:bg-gray-50 transition-colors"
                >
                  <RefreshCw size={18} />
                  Reload Page
                </button>
                <button
                  onClick={this.handleGoHome}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-3 border-2 border-gray-200 text-gray-700 rounded-xl font-semibold hover:bg-gray-50 transition-colors"
                >
                  <Home size={18} />
                  Go Home
                </button>
              </div>

              {/* Support Info */}
              <div className="mt-6 pt-4 border-t text-center">
                <p className="text-sm text-gray-500">
                  If this problem persists, please contact support:
                </p>
                <a 
                  href="mailto:support@texashomeoutlet.com"
                  className="text-blue-600 hover:text-blue-700 font-medium"
                >
                  support@texashomeoutlet.com
                </a>
              </div>
            </div>
          </div>
        </div>
      );
    }

    // Render children normally
    return this.props.children;
  }
}

/**
 * Simple error fallback for smaller components
 */
export function ComponentErrorFallback({ _error, resetError }) {
  return (
    <div className="p-4 bg-red-50 border border-red-200 rounded-xl">
      <div className="flex items-center gap-2 text-red-600 mb-2">
        <AlertTriangle size={20} />
        <span className="font-semibold">Component Error</span>
      </div>
      <p className="text-sm text-red-600 mb-3">
        This section encountered an error. You can try reloading it.
      </p>
      <button
        onClick={resetError}
        className="text-sm px-3 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
      >
        Retry
      </button>
    </div>
  );
}

export default ErrorBoundary;
