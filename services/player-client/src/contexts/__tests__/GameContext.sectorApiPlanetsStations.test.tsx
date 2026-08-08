// @vitest-environment jsdom
/**
 * GameContext — sectorAPI.getPlanets / getStations (WO-WIRE-SECTOR-API-PLANETS-STATIONS).
 *
 * exploreCurrentLocation must call the shared sectorAPI wrappers (not raw
 * apiClient.get against the same URLs) so the api.ts bindings stay live.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost, mockGetPlanets, mockGetStations } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetPlanets: vi.fn(),
  mockGetStations: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost },
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api');
  return {
    ...actual,
    sectorAPI: {
      ...actual.sectorAPI,
      getPlanets: (...a: unknown[]) => mockGetPlanets(...a),
      getStations: (...a: unknown[]) => mockGetStations(...a),
    },
  };
});

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1' }, isAuthenticated: true }),
}));

import { GameProvider, useGame } from '../GameContext';

function defaultGet(url: string) {
  if (url === '/api/v1/first-login/status') {
    return Promise.resolve({ data: { requires_first_login: false } });
  }
  if (url === '/api/v1/player/state') {
    return Promise.resolve({
      data: {
        id: 'player-1',
        username: 'tester',
        credits: 1000,
        turns: 10,
        max_turns: 500,
        current_sector_id: 42,
        is_docked: false,
        is_landed: false,
        defense_drones: 0,
        attack_drones: 0,
        mines: 0,
        personal_reputation: 0,
        reputation_tier: 'unknown',
        name_color: '#fff',
        military_rank: 'Recruit',
      },
    });
  }
  if (url === '/api/v1/player/ships') {
    return Promise.resolve({ data: [] });
  }
  if (url === '/api/v1/player/current-sector') {
    return Promise.resolve({ data: { sector_id: 42, name: 'Test Sector' } });
  }
  if (url === '/api/v1/quantum/status') {
    return Promise.resolve({
      data: {
        quantum_charges: 0,
        quantum_shards: 0,
        charge_capacity: 0,
        refine_cooldown_ends_at: null,
      },
    });
  }
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext sectorAPI planets/stations', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockGetPlanets.mockResolvedValue({
      planets: [{ id: 'p1', name: 'Alpha', sector_id: 42 }],
    });
    mockGetStations.mockResolvedValue({
      stations: [{ id: 's1', name: 'Port One', sector_id: 42 }],
    });
    captured = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <GameProvider>
          <Consumer />
        </GameProvider>,
      );
      await flush();
      await flush();
      await flush();
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('exploreCurrentLocation loads planets + stations via sectorAPI', async () => {
    expect(mockGetPlanets).toHaveBeenCalledWith(42);
    expect(mockGetStations).toHaveBeenCalledWith(42);
    expect(captured?.planetsInSector?.some((p) => p.id === 'p1')).toBe(true);
    expect(captured?.stationsInSector?.some((s) => s.id === 's1')).toBe(true);
    // Must not hit the raw axios paths for these two endpoints.
    const planetGets = mockGet.mock.calls.filter((c) =>
      String(c[0]).includes('/sectors/') && String(c[0]).includes('/planets'),
    );
    const stationGets = mockGet.mock.calls.filter((c) =>
      String(c[0]).includes('/sectors/') && String(c[0]).includes('/stations'),
    );
    expect(planetGets).toHaveLength(0);
    expect(stationGets).toHaveLength(0);
  });
});
