import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import AppLayout from './AppLayout';

const authState = vi.hoisted(() => ({
  isLoading: false,
  isAuthenticated: true,
}));

const retryConnection = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

const wsState = vi.hoisted(() => ({
  isConnected: true,
  hasGivenUp: false,
  reconnectAttempt: 0,
  maxReconnectAttempts: 5,
  retryConnection,
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsState,
}));

vi.mock('./Sidebar', () => ({
  default: () => <aside data-testid="sidebar" />,
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/login" element={<div>login-outlet</div>} />
          <Route path="/dashboard" element={<div>dashboard-outlet</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('AppLayout auth/ws shell (LEG-3300)', () => {
  beforeEach(() => {
    authState.isLoading = false;
    authState.isAuthenticated = true;
    wsState.isConnected = true;
    wsState.hasGivenUp = false;
    wsState.reconnectAttempt = 0;
    retryConnection.mockReset();
    retryConnection.mockResolvedValue(undefined);
  });

  it('renders Outlet on /login without auth spinner', () => {
    authState.isLoading = true;
    authState.isAuthenticated = false;
    renderAt('/login');

    expect(screen.getByText('login-outlet')).toBeTruthy();
    expect(screen.queryByText('Loading authentication...')).toBeNull();
    expect(screen.queryByTestId('sidebar')).toBeNull();
  });

  it('redirects unauthenticated non-login paths to /login', async () => {
    authState.isAuthenticated = false;
    authState.isLoading = false;
    renderAt('/dashboard');

    await waitFor(() => {
      expect(screen.getByText('login-outlet')).toBeTruthy();
    });
    expect(screen.queryByText('dashboard-outlet')).toBeNull();
  });

  it('shows hasGivenUp banner until dismiss and invokes retryConnection', async () => {
    wsState.hasGivenUp = true;
    wsState.isConnected = false;
    renderAt('/dashboard');

    expect(screen.getByTestId('ws-gave-up-banner')).toBeTruthy();
    expect(screen.getByText(/Live updates disconnected/i)).toBeTruthy();

    fireEvent.click(screen.getByTestId('ws-gave-up-retry'));
    await waitFor(() => expect(retryConnection).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId('ws-gave-up-dismiss'));
    expect(screen.queryByTestId('ws-gave-up-banner')).toBeNull();
  });
});
