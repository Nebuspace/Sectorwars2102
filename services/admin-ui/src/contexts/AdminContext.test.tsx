import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AdminProvider, useAdmin } from './AdminContext';
import { api } from '../utils/auth';
import { wipeBangGalaxy } from '../services/bangGalaxyApi';

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

function httpErr(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });
}

function Probe() {
  const {
    adminStats,
    loadAdminStats,
    users,
    loadUsers,
    error,
    wipeGalaxy,
    clearGalaxyData,
    addSectors,
    createWarpTunnel,
  } = useAdmin();
  return (
    <div>
      <span data-testid="total-users">{adminStats?.totalUsers ?? 'none'}</span>
      <span data-testid="user-count">{users.length}</span>
      <span data-testid="error">{error ?? 'none'}</span>
      <button onClick={() => loadAdminStats()}>load-stats</button>
      <button onClick={() => loadUsers()}>load-users</button>
      <button
        onClick={() => {
          void wipeGalaxy('g1', 'CONFIRM').catch(() => undefined);
        }}
      >
        wipe-galaxy
      </button>
      <button
        onClick={() => {
          void clearGalaxyData().catch(() => undefined);
        }}
      >
        clear-galaxy-data
      </button>
      <button
        onClick={() => {
          void addSectors('g1', 1).catch(() => undefined);
        }}
      >
        add-sectors
      </button>
      <button
        onClick={() => {
          void createWarpTunnel(1, 2, 0.75).catch(() => undefined);
        }}
      >
        create-warp-tunnel
      </button>
    </div>
  );
}

describe('AdminContext / AdminProvider', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.mocked(wipeBangGalaxy).mockReset();
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

  it('surfaces loadAdminStats 403 as PLAYERS_VIEW denial (LEG-1254)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/PLAYERS_VIEW/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load admin statistics/,
    );
  });

  it('surfaces loadUsers 429 as admin rate-limit (LEG-1254)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-users'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
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

  it('surfaces wipeGalaxy 403 as admin.universe.manage denial (LEG-1315)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(wipeBangGalaxy).mockRejectedValueOnce(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('wipe-galaxy'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.universe\.manage|Access denied/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to wipe galaxy');
  });

  it('surfaces wipeGalaxy 429 as admin rate-limit (LEG-1315)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(wipeBangGalaxy).mockRejectedValueOnce(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('wipe-galaxy'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to wipe galaxy');
  });

  it('surfaces clearGalaxyData 403 as admin.galaxy.manage denial (LEG-2780)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.delete).mockRejectedValueOnce(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('clear-galaxy-data'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage|Access denied/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to clear galaxy data');
    expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/galaxy/clear');
  });

  it('surfaces clearGalaxyData 429 as admin rate-limit (LEG-2780)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.delete).mockRejectedValueOnce(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('clear-galaxy-data'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to clear galaxy data');
  });

  it('surfaces addSectors 403 as admin.galaxy.manage denial (LEG-2781)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.post).mockRejectedValueOnce(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('add-sectors'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage|Access denied/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to add sectors to galaxy');
    expect(api.post).toHaveBeenCalledWith('/api/v1/admin/galaxy/g1/sectors/add', {
      num_sectors: 1,
      config: undefined,
    });
  });

  it('surfaces addSectors 429 as admin rate-limit (LEG-2781)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.post).mockRejectedValueOnce(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('add-sectors'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to add sectors to galaxy');
  });

  it('surfaces createWarpTunnel 403 as admin.galaxy.manage denial (LEG-2782)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.post).mockRejectedValueOnce(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('create-warp-tunnel'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage|Access denied/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to create warp tunnel');
    expect(api.post).toHaveBeenCalledWith('/api/v1/admin/warp-tunnels/create', {
      source_sector_id: 1,
      target_sector_id: 2,
      stability: 0.75,
    });
  });

  it('surfaces createWarpTunnel 429 as admin rate-limit (LEG-2782)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.post).mockRejectedValueOnce(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('create-warp-tunnel'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error')).not.toHaveTextContent('Failed to create warp tunnel');
  });
});
