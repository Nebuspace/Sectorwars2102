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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

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

async function saveWithUsernameChange() {
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
}

/**
 * LEG-3682 Soft-ORDER — PlayerDetailEditor TypeError/Network Error densify.
 */
describe('PlayerDetailEditor typeErrorHonesty densify (LEG-3682)', () => {
  const onClose = vi.fn();
  const onSave = vi.fn();

  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    onClose.mockReset();
    onSave.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on teams load without leaking raw transport text', async () => {
    mockMetaLoads({ teamsReject: new Error('Network Error') });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/Failed to load teams/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(screen.getByRole('option', { name: 'No Team' })).toBeTruthy();
  });

  it('collapses TypeError Failed to fetch on teams load without leaking transport text', async () => {
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

  it('collapses axios Network Error on regions load without leaking raw transport text', async () => {
    mockMetaLoads({ regionsReject: new Error('Network Error') });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions');
    });

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/Failed to load regions/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(screen.getByRole('option', { name: 'No Region' })).toBeTruthy();
  });

  it('collapses TypeError Failed to fetch on regions load without leaking transport text', async () => {
    mockMetaLoads({ regionsReject: new TypeError('Failed to fetch') });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/regions');
    });

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/Failed to load regions/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(screen.getByRole('option', { name: 'No Region' })).toBeTruthy();
  });

  it('collapses axios Network Error on player PATCH save without leaking raw transport text', async () => {
    mockMetaLoads();
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    await saveWithUsernameChange();

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/Failed to update player/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('collapses TypeError Failed to fetch on player PATCH save without leaking transport text', async () => {
    mockMetaLoads();
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    await saveWithUsernameChange();

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/Failed to update player/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('surfaces 403 with PLAYERS_VIEW scope hint when teams GET is denied', async () => {
    mockMetaLoads({ teamsReject: axiosError(403) });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on teams GET', async () => {
    mockMetaLoads({ teamsReject: axiosError(429) });

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 403 with scope-aware copy on player PATCH save', async () => {
    mockMetaLoads();
    vi.mocked(api.patch).mockRejectedValue(axiosError(403));

    render(
      <PlayerDetailEditor player={basePlayer} onClose={onClose} onSave={onSave} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/teams');
    });

    await saveWithUsernameChange();

    const alert = await screen.findByRole('alert');
    const text = alert.textContent ?? '';
    expect(text).toMatch(/PLAYERS_ADJUST|PLAYERS_SUSPEND|Access denied/i);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
