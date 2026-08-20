import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AdminProvider, useAdmin } from './AdminContext';
import { api } from '../utils/auth';

const mockUseAuth = vi.fn();
vi.mock('./AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('../services/bangGalaxyApi', () => ({
  createBangJob: vi.fn(),
  listBangJobs: vi.fn(),
  wipeBangGalaxy: vi.fn(),
}));

vi.mock('../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

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
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it('does not fetch admin stats for a non-admin user (loadAdminStats is a no-op)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: false }, token: 'tok' });
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    expect(api.get).not.toHaveBeenCalled();
    expect(screen.getByTestId('total-users')).toHaveTextContent('none');
  });

  it('loads and maps admin stats from snake_case to camelCase for an admin user', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockResolvedValue({
      data: { total_users: 42, total_players: 10, total_sectors: 1, total_planets: 2, total_ports: 3, total_ships: 4, active_sessions: 5 },
    });
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    await waitFor(() => expect(screen.getByTestId('total-users')).toHaveTextContent('42'));
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/stats');
  });

  it('sets an error and clears stats when loadAdminStats fails', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(new Error('network down'));
    const user = userEvent.setup();

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
    vi.mocked(api.get).mockResolvedValue({ data: { users: [{ id: 'u1' }, { id: 'u2' }] } });
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-users'));
    await waitFor(() => expect(screen.getByTestId('user-count')).toHaveTextContent('2'));
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/users');
  });
});
