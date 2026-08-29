import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AdminProvider, useAdmin } from './AdminContext';
import { api } from '../utils/auth';
import { listBangJobs, createBangJob, wipeBangGalaxy } from '../services/bangGalaxyApi';

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

const minimalBangConfig = {
  seed: 1,
  sectors: 100,
  region_type: 'player_owned' as const,
};

type ApiGetResult = ReturnType<typeof api.get>;

function mockAdminGetDefaults(overrides?: (url: string) => ApiGetResult | void) {
  vi.mocked(api.get).mockImplementation((url: string) => {
    const custom = overrides?.(url);
    if (custom !== undefined) return custom;
    if (url === '/api/v1/admin/galaxy') {
      return Promise.resolve({ data: { id: 'g1', name: 'Test Galaxy' } });
    }
    if (url === '/api/v1/admin/stats') {
      return Promise.resolve({
        data: {
          total_users: 0,
          total_players: 0,
          total_sectors: 0,
          total_planets: 0,
          total_ports: 0,
          total_ships: 0,
          active_sessions: 0,
        },
      });
    }
    if (url === '/api/v1/admin/users') {
      return Promise.resolve({ data: { users: [] } });
    }
    if (url === '/api/v1/admin/players') {
      return Promise.resolve({ data: { players: [] } });
    }
    if (url === '/api/v1/admin/regions') {
      return Promise.resolve({ data: { regions: [] } });
    }
    if (url === '/api/v1/admin/regions/r1/zones') {
      return Promise.resolve({ data: { zones: [] } });
    }
    return Promise.resolve({ data: {} });
  });
}

function Probe() {
  const {
    adminStats,
    loadAdminStats,
    users,
    loadUsers,
    loadRegions,
    loadRegionZones,
    loadPlayers,
    activateUser,
    deactivateUser,
    error,
    wipeGalaxy,
    galaxyState,
    loadGalaxyInfo,
    loadSectors,
    loadBangHistory,
    clearGalaxyData,
    addSectors,
    createWarpTunnel,
    bangGalaxy,
  } = useAdmin();
  return (
    <div>
      <span data-testid="total-users">{adminStats?.totalUsers ?? 'none'}</span>
      <span data-testid="user-count">{users.length}</span>
      <span data-testid="galaxy-loaded">{galaxyState ? 'yes' : 'no'}</span>
      <span data-testid="error">{error ?? 'none'}</span>
      <button onClick={() => loadAdminStats()}>load-stats</button>
      <button onClick={() => loadUsers()}>load-users</button>
      <button onClick={() => void loadRegions()}>load-regions</button>
      <button onClick={() => void loadRegionZones('r1')}>load-region-zones</button>
      <button onClick={() => void loadPlayers()}>load-players</button>
      <button
        onClick={() => {
          void activateUser('u1').catch(() => undefined);
        }}
      >
        activate-user
      </button>
      <button
        onClick={() => {
          void deactivateUser('u1').catch(() => undefined);
        }}
      >
        deactivate-user
      </button>
      <button onClick={() => void loadGalaxyInfo()}>load-galaxy-info</button>
      <button onClick={() => void loadSectors()}>load-sectors</button>
      <button
        onClick={() => {
          void loadBangHistory().catch(() => undefined);
        }}
      >
        load-bang-history
      </button>
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
      <button
        onClick={() => {
          void bangGalaxy(minimalBangConfig, 'Test Galaxy').catch(() => undefined);
        }}
      >
        bang-galaxy
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
    vi.mocked(listBangJobs).mockReset();
    vi.mocked(createBangJob).mockReset();
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

  it('surfaces loadAdminStats 429 as admin rate-limit (LEG-2963)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(api.get).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>
    );

    await user.click(screen.getByText('load-stats'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
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

  it('surfaces loadUsers 403 as PLAYERS_VIEW denial (LEG-2812)', async () => {
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

  it('surfaces loadRegions 403 as admin.galaxy.manage denial (LEG-2810)', async () => {
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

  it('surfaces loadRegions 429 as admin rate-limit (LEG-2810)', async () => {
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

  it('surfaces loadRegionZones 403 as admin.galaxy.manage denial (LEG-2822)', async () => {
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
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions/r1/zones');
  });

  it('surfaces loadRegionZones 429 as admin rate-limit (LEG-2822)', async () => {
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
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions/r1/zones');
  });

  it('surfaces loadPlayers 403 as PLAYERS_VIEW denial (LEG-2822)', async () => {
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
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load player accounts/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players');
  });

  it('surfaces loadPlayers 429 as admin rate-limit (LEG-2822)', async () => {
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
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load player accounts/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players');
  });

  it('surfaces activateUser 403 as PLAYERS_VIEW denial (LEG-2811)', async () => {
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

  it('surfaces activateUser 429 as admin rate-limit (LEG-2811)', async () => {
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
  });

  it('surfaces deactivateUser 403 as PLAYERS_VIEW denial (LEG-2811)', async () => {
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

  it('surfaces deactivateUser 429 as admin rate-limit (LEG-2811)', async () => {
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

  it('surfaces loadSectors 403 as admin.galaxy.manage denial (LEG-2802)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    mockAdminGetDefaults((url) => {
      if (url === '/api/v1/admin/sectors') {
        return Promise.reject(httpErr(403));
      }
    });
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('galaxy-loaded')).toHaveTextContent('yes'));
    await user.click(screen.getByText('load-sectors'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/admin\.galaxy\.manage/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load sectors/);
  });

  it('surfaces loadSectors 429 as admin rate-limit (LEG-2802)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    mockAdminGetDefaults((url) => {
      if (url === '/api/v1/admin/sectors') {
        return Promise.reject(httpErr(429));
      }
    });
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('galaxy-loaded')).toHaveTextContent('yes'));
    await user.click(screen.getByText('load-sectors'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(/Failed to load sectors/);
  });

  it('surfaces loadBangHistory 403 as BANG_REGENERATE denial (LEG-2803)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(listBangJobs).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>,
    );

    await user.click(screen.getByText('load-bang-history'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/BANG_REGENERATE/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load bang generation history/,
    );
  });

  it('surfaces loadBangHistory 429 as admin rate-limit (LEG-2803)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(listBangJobs).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>,
    );

    await user.click(screen.getByText('load-bang-history'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to load bang generation history/,
    );
  });

  it('surfaces bangGalaxy 403 as BANG_REGENERATE denial (LEG-2806)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(createBangJob).mockRejectedValue(httpErr(403));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>,
    );

    await user.click(screen.getByText('bang-galaxy'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/BANG_REGENERATE/),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to start bang generation job/,
    );
    expect(createBangJob).toHaveBeenCalledWith(
      { config: minimalBangConfig, galaxy_name: 'Test Galaxy' },
      'tok',
    );
  });

  it('surfaces bangGalaxy 429 as admin rate-limit (LEG-2806)', async () => {
    mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
    vi.mocked(createBangJob).mockRejectedValue(httpErr(429));
    const user = userEvent.setup();

    render(
      <AdminProvider>
        <Probe />
      </AdminProvider>,
    );

    await user.click(screen.getByText('bang-galaxy'));
    await waitFor(() =>
      expect(screen.getByTestId('error').textContent).toMatch(/rate limit/i),
    );
    expect(screen.getByTestId('error').textContent).not.toMatch(
      /Failed to start bang generation job/,
    );
  });
});
