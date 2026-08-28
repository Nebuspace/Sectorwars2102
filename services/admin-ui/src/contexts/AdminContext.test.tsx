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
    loadGalaxyInfo,
    loadRegions,
    activateUser,
    deactivateUser,
    loadRegionZones,
    loadPlayers,
  } = useAdmin();
  return (
    <div>
      <span data-testid="total-users">{adminStats?.totalUsers ?? 'none'}</span>
      <span data-testid="user-count">{users.length}</span>
      <span data-testid="error">{error ?? 'none'}</span>
      <button onClick={() => loadAdminStats()}>load-stats</button>
      <button onClick={() => loadUsers()}>load-users</button>
      <button onClick={() => loadGalaxyInfo()}>load-galaxy-info</button>
      <button onClick={() => loadRegions()}>load-regions</button>
      <button onClick={() => activateUser('u1')}>activate-user</button>
      <button onClick={() => deactivateUser('u1')}>deactivate-user</button>
      <button onClick={() => loadRegionZones('region-1')}>load-region-zones</button>
      <button onClick={() => loadPlayers()}>load-players</button>
      <button
        onClick={() => {
          void wipeGalaxy('g1', 'CONFIRM').catch(() => undefined);
        }}
      >
        wipe-galaxy
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

  it('surfaces loadUsers 403 as PLAYERS_VIEW denial (LEG-2796)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-users'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/PLAYERS_VIEW/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load user accounts/,
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/users');
  });

  it('surfaces loadGalaxyInfo 403 as admin.galaxy.manage denial (LEG-2790)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-galaxy-info'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load galaxy information/,
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/galaxy');
  });

  it('surfaces loadGalaxyInfo 429 as admin rate-limit (LEG-2790)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-galaxy-info'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load galaxy information/,
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/galaxy');
  });

  it('surfaces loadRegions 403 as admin.galaxy.manage denial (LEG-2794)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-regions'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load regions/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions');
  });

  it('surfaces loadRegions 429 as admin rate-limit (LEG-2794)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-regions'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load regions/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions');
  });

  it('surfaces activateUser 403 as PLAYERS_VIEW denial (LEG-2795)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.put).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('activate-user'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/PLAYERS_VIEW/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to activate user account/,
    );
    expect(api.put).toHaveBeenCalledWith('/api/v1/users/u1', { is_active: true });
  });

  it('surfaces activateUser 429 as admin rate-limit (LEG-2795)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.put).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('activate-user'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to activate user account/,
    );
    expect(api.put).toHaveBeenCalledWith('/api/v1/users/u1', { is_active: true });
  });

  it('surfaces deactivateUser 403 as PLAYERS_VIEW denial (LEG-2795)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.put).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('deactivate-user'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/PLAYERS_VIEW/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to deactivate user account/,
    );
    expect(api.put).toHaveBeenCalledWith('/api/v1/users/u1', { is_active: false });
  });

  it('surfaces deactivateUser 429 as admin rate-limit (LEG-2795)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.put).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('deactivate-user'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to deactivate user account/,
    );
    expect(api.put).toHaveBeenCalledWith('/api/v1/users/u1', { is_active: false });
  });

  it('surfaces loadRegionZones 403 as admin.galaxy.manage denial (LEG-2797)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-region-zones'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load region zones/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions/region-1/zones');
  });

  it('surfaces loadRegionZones 429 as admin rate-limit (LEG-2797)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-region-zones'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load region zones/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions/region-1/zones');
  });

  it('surfaces loadPlayers 403 as PLAYERS_VIEW denial (LEG-2799)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-players'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/PLAYERS_VIEW/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load player accounts/,
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players');
  });

  it('surfaces loadPlayers 429 as admin rate-limit (LEG-2799)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-players'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load player accounts/,
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players');
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
});
