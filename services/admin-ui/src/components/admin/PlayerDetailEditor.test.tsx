import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlayerDetailEditor from './PlayerDetailEditor';
import { api } from '../../utils/auth';
import { PlayerModel } from '../../types/playerManagement';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

const basePlayer: PlayerModel = {
  id: 'p1',
  username: 'TestUser',
  email: 'test@example.com',
  credits: 1000,
  turns: 50,
  current_sector_id: 1,
  current_region_id: null,
  current_ship_id: null,
  team_id: null,
  is_active: true,
  last_login: null,
  created_at: '2026-01-01T00:00:00Z',
  ships_count: 0,
  planets_count: 0,
  stations_count: 0,
  status: 'active',
  assets: {
    ships_count: 0,
    planets_count: 0,
    stations_count: 0,
    total_value: 0,
  },
  activity: {
    last_login: null,
    session_count_today: 0,
    actions_today: 0,
    total_trade_volume: 0,
    combat_rating: 0,
    suspicious_activity: false,
  },
  aria: null,
};

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function mockMetaLoads({
  teamsReject,
  regionsReject,
}: {
  teamsReject?: unknown;
  regionsReject?: unknown;
} = {}) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url === '/api/v1/admin/teams') {
      if (teamsReject) {
        throw teamsReject;
      }
      return { data: { teams: [] } };
    }
    if (url === '/api/v1/admin/regions') {
      if (regionsReject) {
        throw regionsReject;
      }
      return { data: { regions: [] } };
    }
    return { data: {} };
  });
}

describe('PlayerDetailEditor (LEG-2721 formatAdminApiError)', () => {
  const onClose = vi.fn();
  const onSave = vi.fn();

  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    onClose.mockReset();
    onSave.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on teams LIST 403 in metaLoadError banner', async () => {
    mockMetaLoads({
      teamsReject: axiosError(403, 'Missing scope admin.players.view'),
    });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent ?? '').toMatch(/Missing scope admin\.players\.view|PLAYERS_VIEW/i);
    expect(screen.getByRole('option', { name: 'No Team' })).toBeTruthy();
  });

  it('shows rate-limit copy on teams LIST 429 in metaLoadError banner', async () => {
    mockMetaLoads({ teamsReject: axiosError(429) });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent ?? '').toMatch(/rate limit/i);
    expect(screen.getByRole('option', { name: 'No Team' })).toBeTruthy();
  });

  it('surfaces honest fallback on teams LIST TypeError/network collapse (LEG-3042)', async () => {
    mockMetaLoads({ teamsReject: new TypeError('Failed to fetch') });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/Failed to load teams/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(screen.getByRole('option', { name: 'No Team' })).toBeTruthy();
  });

  it('surfaces scope denial on regions LIST 403 in metaLoadError banner', async () => {
    mockMetaLoads({
      regionsReject: axiosError(403, 'Missing scope admin.players.view'),
    });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions');
    });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent ?? '').toMatch(/Missing scope admin\.players\.view|PLAYERS_VIEW/i);
    expect(screen.getByRole('option', { name: 'No Region' })).toBeTruthy();
  });

  it('shows rate-limit copy on regions LIST 429 in metaLoadError banner', async () => {
    mockMetaLoads({ regionsReject: axiosError(429) });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions');
    });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent ?? '').toMatch(/rate limit/i);
    expect(screen.getByRole('option', { name: 'No Region' })).toBeTruthy();
  });

  it('surfaces formatAdminApiError on player PATCH 403 on save', async () => {
    mockMetaLoads();
    vi.mocked(api.patch).mockRejectedValue(axiosError(403));

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    fireEvent.change(screen.getByDisplayValue('TestUser'), {
      target: { value: 'UpdatedName' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/players/p1',
        expect.objectContaining({ username: 'UpdatedName' }),
      );
    });

    // findByRole: React setErrors after rejected PATCH is async — getByRole raced in CI.
    const alert = await screen.findByRole('alert');
    expect(alert.textContent ?? '').toMatch(
      /PLAYERS_ADJUST_CREDITS|PLAYERS_SUSPEND|PLAYERS_ADJUST_REP|Access denied/i,
    );
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('surfaces rate-limit copy on player PATCH 429 on save', async () => {
    mockMetaLoads();
    vi.mocked(api.patch).mockRejectedValue(axiosError(429));

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    fireEvent.change(screen.getByDisplayValue('test@example.com'), {
      target: { value: 'new@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/players/p1',
        expect.objectContaining({ email: 'new@example.com' }),
      );
    });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent ?? '').toMatch(/rate limit/i);
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
