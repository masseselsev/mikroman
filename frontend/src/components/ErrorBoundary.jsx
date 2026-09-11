import React from 'react';

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('[MikroMan ErrorBoundary caught error]:', error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    try {
      localStorage.clear();
      sessionStorage.clear();
    } catch {
      // ignore
    }
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--bg-primary, #0e1218)',
            color: 'var(--text-primary, #f0f4fc)',
            padding: 24,
            fontFamily: "var(--font-sans, 'Inter', sans-serif)",
          }}
        >
          <div
            style={{
              maxWidth: 480,
              width: '100%',
              background: 'var(--bg-card, #1c222e)',
              border: '1px solid var(--border-color, #2a3344)',
              borderRadius: 'var(--radius-xl, 20px)',
              padding: 32,
              textAlign: 'center',
              boxShadow: '0 24px 64px rgba(0, 0, 0, 0.55)',
            }}
          >
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: '50%',
                background: 'rgba(239, 68, 68, 0.15)',
                color: '#ef4444',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 16px auto',
                fontSize: 24,
              }}
            >
              ⚠️
            </div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: '0 0 8px 0' }}>
              Something went wrong
            </h2>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary, #9da8be)', margin: '0 0 20px 0', lineHeight: 1.5 }}>
              An unexpected render error occurred. You can reload the page or reset cached state.
            </p>
            {this.state.error && (
              <pre
                style={{
                  background: 'var(--bg-input, #12161f)',
                  padding: 12,
                  borderRadius: 'var(--radius-sm, 6px)',
                  fontSize: '0.75rem',
                  color: '#ef4444',
                  textAlign: 'left',
                  overflowX: 'auto',
                  maxHeight: 120,
                  marginBottom: 24,
                }}
              >
                {this.state.error.message || String(this.state.error)}
              </pre>
            )}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
              <button
                type="button"
                onClick={this.handleReload}
                style={{
                  background: 'var(--color-primary, #0b72c9)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 'var(--radius-sm, 6px)',
                  padding: '8px 18px',
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  cursor: 'pointer',
                }}
              >
                Reload Page
              </button>
              <button
                type="button"
                onClick={this.handleReset}
                style={{
                  background: 'transparent',
                  color: 'var(--text-secondary, #9da8be)',
                  border: '1px solid var(--border-color, #2a3344)',
                  borderRadius: 'var(--radius-sm, 6px)',
                  padding: '8px 18px',
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  cursor: 'pointer',
                }}
              >
                Reset Cache & Reload
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
