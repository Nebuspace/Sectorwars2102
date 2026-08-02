import React, { useState, useEffect } from 'react';
import { Outlet, useLocation, Navigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import Sidebar from './Sidebar';

const AppLayout: React.FC = () => {
  const { isLoading, isAuthenticated } = useAuth();
  const {
    isConnected,
    hasGivenUp,
    reconnectAttempt,
    maxReconnectAttempts,
    retryConnection,
  } = useWebSocket();
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(true);
  const [isMobile, setIsMobile] = useState<boolean>(false);
  const [loadingTimeout, setLoadingTimeout] = useState<boolean>(false);
  const [gaveUpDismissed, setGaveUpDismissed] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const location = useLocation();

  // Re-show banner if gave-up flips true again after a prior dismiss.
  useEffect(() => {
    if (hasGivenUp) {
      setGaveUpDismissed(false);
    }
  }, [hasGivenUp]);

  // Check if we're on the login page
  const isLoginPage = location.pathname === '/login';

  // Handle responsive sidebar
  useEffect(() => {
    const checkScreenSize = () => {
      setIsMobile(window.innerWidth < 992);
      setSidebarOpen(window.innerWidth >= 992);
    };

    checkScreenSize();
    window.addEventListener('resize', checkScreenSize);

    return () => {
      window.removeEventListener('resize', checkScreenSize);
    };
  }, []);

  // Close sidebar when changing routes on mobile
  useEffect(() => {
    if (isMobile) {
      setSidebarOpen(false);
    }
  }, [location.pathname, isMobile]);
  
  // Add timeout for loading state
  useEffect(() => {
    let timer: number;
    if (isLoading && !isLoginPage) {
      timer = window.setTimeout(() => {
        setLoadingTimeout(true);
      }, 3000); // 3 second timeout
    }
    
    return () => {
      window.clearTimeout(timer);
    };
  }, [isLoading, isLoginPage]);

  // Special case for login page - never show loading spinner on login page
  if (isLoginPage) {
    return (
      <div className="app-layout">
        <main className="main-content">
          <Outlet />
        </main>
      </div>
    );
  }
  
  // Redirect to login if not authenticated and not already loading,
  // preserving the intended destination (see ProtectedRoute for the
  // matching treatment on the nested-route guard).
  if (!isLoading && !isAuthenticated && !isLoginPage) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Show loading state for other pages
  if (isLoading) {
    if (loadingTimeout) {
      return (
        <div className="app-layout">
          <main className="main-content">
            <div className="alert alert-error">
              <h2>Authentication Timeout</h2>
              <p>We couldn&apos;t authenticate you automatically. Please log in again.</p>
              <Navigate to="/login" state={{ from: location }} replace />
            </div>
          </main>
        </div>
      );
    }
    
    return (
      <div className="loading-state">
        <div className="spinner"></div>
        <p>Loading authentication...</p>
      </div>
    );
  }

  return (
    <div className="app-layout">
      {/* Don't show sidebar on login page or if not authenticated */}
      {!isLoginPage && isAuthenticated && (
        <>
          <Sidebar />

          {isMobile && (
            <button
              className={`sidebar-toggle ${sidebarOpen ? 'open' : ''}`}
              onClick={() => setSidebarOpen(!sidebarOpen)}
              style={{
                position: 'fixed',
                top: 'var(--space-4)',
                left: 'var(--space-4)',
                zIndex: 'calc(var(--z-fixed) + 1)',
                background: 'var(--interactive-primary)',
                color: 'white',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                padding: 'var(--space-2) var(--space-3)',
                fontSize: 'var(--font-size-lg)'
              }}
            >
              {sidebarOpen ? '×' : '☰'}
            </button>
          )}
        </>
      )}

      <main className="main-content">
        {/* Abandoned reconnect — visible without DevTools (WO-ADM-WS-GAVEUP-BANNER) */}
        {isAuthenticated && !isLoginPage && hasGivenUp && !gaveUpDismissed && (
          <div
            data-testid="ws-gave-up-banner"
            role="status"
            className="ws-gave-up-banner"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px',
              margin: '0 0 12px',
              padding: '12px 16px',
              borderRadius: '6px',
              background: 'var(--warning, #b45309)',
              color: 'white',
              fontSize: '14px',
            }}
          >
            <div>
              <strong>Live updates disconnected.</strong>
              {' '}
              Automatic reconnection was abandoned after repeated failures.
            </div>
            <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
              <button
                type="button"
                className="btn btn-secondary"
                data-testid="ws-gave-up-retry"
                disabled={retrying}
                onClick={async () => {
                  setRetrying(true);
                  try {
                    await retryConnection();
                  } finally {
                    setRetrying(false);
                  }
                }}
                style={{
                  background: 'white',
                  color: 'var(--warning, #b45309)',
                  border: 'none',
                  borderRadius: '4px',
                  padding: '6px 12px',
                  cursor: retrying ? 'wait' : 'pointer',
                  fontWeight: 600,
                }}
              >
                {retrying ? 'Retrying…' : 'Retry connection'}
              </button>
              <button
                type="button"
                data-testid="ws-gave-up-dismiss"
                aria-label="Dismiss connection warning"
                onClick={() => setGaveUpDismissed(true)}
                style={{
                  background: 'transparent',
                  color: 'white',
                  border: '1px solid rgba(255,255,255,0.6)',
                  borderRadius: '4px',
                  padding: '6px 12px',
                  cursor: 'pointer',
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {/* WebSocket connection status - only show when connected or actively reconnecting */}
        {isAuthenticated && !isLoginPage && !hasGivenUp && (
          <div
            className="connection-status"
            data-testid="ws-connection-chip"
            style={{
            position: 'fixed',
            bottom: '20px',
            right: '20px',
            padding: '8px 16px',
            borderRadius: '4px',
            background: isConnected ? 'var(--success)' : 'var(--error)',
            color: 'white',
            fontSize: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            zIndex: 1000,
            opacity: 0.9
          }}>
            <span style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: 'currentColor',
              display: 'inline-block',
              animation: isConnected ? 'pulse 2s infinite' : 'none'
            }}></span>
            {isConnected
              ? 'Live Updates Active'
              : reconnectAttempt > 0
                ? `Reconnecting… (${reconnectAttempt}/${maxReconnectAttempts})`
                : 'Reconnecting…'}
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
};

export default AppLayout;