import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlayerAssetManager from './PlayerAssetManager';
import { api } from '../../utils/auth';
import type { PlayerModel } from '../../types/playerManagement';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const player = {
  id: 'p1',
  username: 'Trader',
  email: 't@example.com',
  credits: 100,
  turns: 10,
  current_sector_id: 1,
  status: 'active',
  team_id: null,
} as PlayerModel;

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('PlayerAssetManager scope honesty (LEG-1207)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces PLAYERS_VIEW denial on 403 instead of silent empty list', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(alert).not.toMatch(/^Failed to load player assets$/);
  });

  it('surfaces admin rate-limit on 429', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2962)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error loading player assets/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toMatch(/^Failed to load player assets$/);
  });

  it('collapses axios-shaped Network Error to gameserver-unreachable fallback (LEG-3313)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error loading player assets/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/^Failed to load player assets$/);
  });
});

describe('PlayerAssetManager pirate-holding indicator (LEG-4193)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  function mockOwnedAssets(overrides?: {
    planets?: unknown[];
    ports?: unknown[];
    sectors?: Array<{ sector_id: number; has_pirate_holding?: boolean }>;
    sectorsReject?: unknown;
  }) {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/admin/ships')) {
        return { data: { ships: [] } };
      }
      if (String(url).includes('/admin/planets')) {
        return {
          data: {
            planets:
              overrides?.planets ?? [
                { id: 'pl1', name: 'Alpha', planet_type: 'Terran', sector_id: 10 },
              ],
          },
        };
      }
      if (String(url).includes('/admin/ports')) {
        return {
          data: {
            ports:
              overrides?.ports ?? [
                { id: 'pt1', name: 'Dock', port_class: 1, sector_id: 10 },
                { id: 'pt2', name: 'Bay', port_class: 2, sector_id: 20 },
              ],
          },
        };
      }
      if (String(url).includes('/admin/sectors')) {
        if (overrides?.sectorsReject) {
          throw overrides.sectorsReject;
        }
        return {
          data: {
            sectors:
              overrides?.sectors ?? [
                { sector_id: 10, has_pirate_holding: true },
                { sector_id: 20, has_pirate_holding: false },
              ],
            total: 2,
          },
        };
      }
      return { data: {} };
    });
  }

  it('shows Holding badge on owned planets/ports whose sector has_pirate_holding', async () => {
    mockOwnedAssets();

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/sectors',
        expect.objectContaining({ params: { page: 1, limit: 100 } }),
      );
    });

    // No per-row pirate-holdings GET
    expect(
      vi
        .mocked(api.get)
        .mock.calls.some(([url]) => String(url).includes('/pirate-holdings')),
    ).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /Planets/i }));
    expect(await screen.findByTestId('pirate-holding-badge-pl1')).toHaveTextContent(
      'Holding',
    );

    fireEvent.click(screen.getByRole('button', { name: /Ports/i }));
    expect(screen.getByTestId('pirate-holding-badge-pt1')).toBeTruthy();
    expect(screen.queryByTestId('pirate-holding-badge-pt2')).toBeNull();
    expect(screen.queryByText(/outlaw_base/i)).toBeNull();
  });

  it('shows no Holding chrome when sector flags are false/omitted', async () => {
    mockOwnedAssets({
      sectors: [
        { sector_id: 10, has_pirate_holding: false },
        { sector_id: 20 },
      ],
    });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/sectors',
        expect.anything(),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /Planets/i }));
    expect(screen.queryByTestId('pirate-holding-badge-pl1')).toBeNull();
  });

  it('surfaces sectors-list failure via formatAdminApiError without inventing badges', async () => {
    mockOwnedAssets({
      sectorsReject: Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: { detail: 'Missing scope' } },
      }),
    });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    const alert = await screen.findByTestId('pirate-holdings-flag-error');
    expect(alert.textContent ?? '').toMatch(/Missing scope|Access denied|admin\.galaxy\.manage/i);
    expect(screen.queryByTestId(/pirate-holding-badge-/)).toBeNull();
  });

  it('skips sectors list when player owns no planets or ports', async () => {
    mockOwnedAssets({ planets: [], ports: [], sectors: [] });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        expect.stringContaining('/admin/ships'),
      );
    });

    expect(
      vi
        .mocked(api.get)
        .mock.calls.some(([url]) => String(url).includes('/admin/sectors')),
    ).toBe(false);
  });
});

describe('PlayerAssetManager owned pirate holdings (LEG-4213)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  function mockBaseAssets(holdingsResponse?: { holdings?: unknown[] } | Error) {
    vi.mocked(api.get).mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (String(url).includes('/admin/ships')) {
        return { data: { ships: [] } };
      }
      if (String(url).includes('/admin/planets')) {
        return { data: { planets: [] } };
      }
      if (String(url).includes('/admin/ports')) {
        return { data: { ports: [] } };
      }
      if (String(url).includes('/admin/sectors')) {
        return { data: { sectors: [], total: 0 } };
      }
      if (String(url) === '/api/v1/admin/pirate-holdings') {
        if (holdingsResponse instanceof Error) throw holdingsResponse;
        return { data: holdingsResponse ?? { holdings: [] } };
      }
      return { data: {} };
    });
  }

  it('loads by-owner GET once when Pirate Holdings tab is opened', async () => {
    mockBaseAssets({
      holdings: [
        {
          id: 'h1',
          tier: 'outpost',
          sector_id: 7,
          outlaw_base_id: 'ob1',
          current_strength: 12,
        },
      ],
    });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/admin/ships'));
    });

    fireEvent.click(screen.getByTestId('owned-pirate-holdings-tab'));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/pirate-holdings', {
        params: { owner_player_id: 'p1' },
      });
    });

    expect(await screen.findByTestId('owned-pirate-holding-row-h1')).toBeTruthy();
    expect(screen.getByText(/sector_id: 7/)).toBeTruthy();
    expect(screen.getByText(/outlaw_base_id: ob1/)).toBeTruthy();

    // One by-owner GET — no sector pirate-holdings N+1
    const pirateCalls = vi
      .mocked(api.get)
      .mock.calls.filter(([url]) => String(url).includes('/pirate-holdings'));
    expect(pirateCalls).toHaveLength(1);
    expect(pirateCalls[0][0]).toBe('/api/v1/admin/pirate-holdings');
  });

  it('shows honest empty state when by-owner list is empty', async () => {
    mockBaseAssets({ holdings: [] });

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/admin/ships'));
    });

    fireEvent.click(screen.getByTestId('owned-pirate-holdings-tab'));

    expect(await screen.findByTestId('owned-pirate-holdings-empty')).toHaveTextContent(
      /No pirate holdings owned/i,
    );
    expect(screen.queryByTestId(/owned-pirate-holding-row-/)).toBeNull();
  });

  it('surfaces by-owner GET failure via formatAdminApiError without fabricating rows', async () => {
    mockBaseAssets(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: { detail: 'Missing PLAYERS_VIEW' } },
      }),
    );

    render(
      <PlayerAssetManager player={player} onClose={() => {}} onUpdate={() => {}} />,
    );

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/admin/ships'));
    });

    fireEvent.click(screen.getByTestId('owned-pirate-holdings-tab'));

    const alert = await screen.findByTestId('owned-pirate-holdings-error');
    expect(alert.textContent ?? '').toMatch(/Missing PLAYERS_VIEW|Access denied/i);
    expect(screen.queryByTestId('owned-pirate-holdings-empty')).toBeNull();
    expect(screen.queryByTestId(/owned-pirate-holding-row-/)).toBeNull();
  });
});
