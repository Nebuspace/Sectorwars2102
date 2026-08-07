import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import { AdminProvider, useAdmin } from './AdminContext';

const mockUseAuth = vi.fn();
vi.mock('./AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('../services/bangGalaxyApi', () => ({
  createBangJob: vi.fn(),
  listBangJobs: vi.fn(),
  wipeBangGalaxy: vi.fn(),
}));

vi.mock('axios');
const mockedAxios = vi.mocked(axios, true);

function Probe() {
  const { adminStats, loadAdminStats, users, loadUsers, error } = useAdmin();
  return (
    <div>
      <span data-testid="total-users">{adminStats?.totalUsers ?? 'none'}</span>
      <span data-testid="user-count">{users.length}</span>
      <span data-testid="error">{error ?? 'none'}</span>
      <button onClick={() => loadAdminStats()}>load-stats</button>
      <button onClick={() => loadUsers()}>load-users</button>
    </div>
  );
}

describe('AdminContext / AdminProvider', () => {
  let mockApiInstance: { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn>; put: ReturnType<typeof vi.fn>; delete: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    mockApiInstance = { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() };
    mockedAxios.create = vi.fn().mockReturnValue(mockApiInstance);
  });

  it('does not fetch admin stats for a non-admin user (loadAdminStats is a no-op)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: false }, token: 'tok' });
    const user = (await import('@testing-library/user-event')).default.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    expect(mockApiInstance.get).not.toHaveBeenCalled();
    expect(screen.getByTestId('total-users')).toHaveTextContent('none');
  });

  it('loads and maps admin stats from snake_case to camelCase for an admin user', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    mockApiInstance.get.mockResolvedValue({
      data: { total_users: 42, total_players: 10, total_sectors: 1, total_planets: 2, total_ports: 3, total_ships: 4, active_sessions: 5 },
    });
    const user = (await import('@testing-library/user-event')).default.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    await waitFor(() => expect(screen.getByTestId('total-users')).toHaveTextContent('42'));
  });

  it('sets an error and clears stats when loadAdminStats fails', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    mockApiInstance.get.mockRejectedValue(new Error('network down'));
    const user = (await import('@testing-library/user-event')).default.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to load admin statistics')
    );
    expect(screen.getByTestId('total-users')).toHaveTextContent('none');
  });

  it('loads user accounts for an admin user', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    mockApiInstance.get.mockResolvedValue({ data: { users: [{ id: 'u1' }, { id: 'u2' }] } });
    const user = (await import('@testing-library/user-event')).default.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-users'));
    await waitFor(() => expect(screen.getByTestId('user-count')).toHaveTextContent('2'));
  });
});
