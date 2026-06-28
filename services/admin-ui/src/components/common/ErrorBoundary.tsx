import React from 'react';

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Global error boundary for admin pages.
 *
 * Catches render-time crashes in any routed page and shows an honest
 * fallback panel (error name + message) instead of a blank screen.
 * Keyed by route in App.tsx so navigating to another page resets it.
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    console.error('Admin UI page crashed:', error, errorInfo.componentStack);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  render(): React.ReactNode {
    if (this.state.error) {
      return (
        <div
          role="alert"
          style={{
            margin: '40px auto',
            maxWidth: '640px',
            padding: '24px',
            background: '#1f2937',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            borderRadius: '8px',
            color: '#e5e7eb'
          }}
        >
          <h2 style={{ margin: '0 0 8px 0', color: '#ef4444' }}>
            This page crashed
          </h2>
          <p style={{ margin: '0 0 12px 0', color: '#9ca3af' }}>
            An unexpected error occurred while rendering this page. The rest of
            the admin console is unaffected.
          </p>
          <pre
            style={{
              margin: '0 0 16px 0',
              padding: '12px',
              background: '#111827',
              border: '1px solid #374151',
              borderRadius: '6px',
              color: '#fca5a5',
              fontSize: '0.85rem',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word'
            }}
          >
            {this.state.error.name}: {this.state.error.message}
          </pre>
          <button
            onClick={this.handleReload}
            style={{
              padding: '8px 16px',
              background: '#374151',
              color: '#e5e7eb',
              border: '1px solid #4b5563',
              borderRadius: '6px',
              cursor: 'pointer'
            }}
          >
            Reload Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
