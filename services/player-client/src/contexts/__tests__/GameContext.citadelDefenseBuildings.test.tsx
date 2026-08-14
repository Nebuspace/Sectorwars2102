// @vitest-environment jsdom
/**
 * GameContext — citadelAPI.getAvailableBuildings / constructBuilding
 * (WO-WIRE-CITADEL-DEFENSE-BUILDINGS).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost, mockGetAvailableBuildings, mockConstructBuilding } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetAvailableBuildings: vi.fn(),
  mockConstructBuilding: vi.fn(),
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
    citadelAPI: {
      ...actual.citadelAPI,
      getAvailableBuildings: (...a: unknown[]) => mockGetAvailableBuildings(...a),
      constructBuilding: (...a: unknown[]) => mockConstructBuilding(...a),
    },
    sectorAPI: {
      ...actual.sectorAPI,
      getPlanets: vi.fn().mockResolvedValue({ planets: [] }),
      getStations: vi.fn().mockResolvedValue({ stations: [] }),
    },
    messageAPI: {
      ...actual.messageAPI,
      getInbox: vi.fn().mockResolvedValue({ messages: [], unread_count: 0 }),
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
        current_sector_id: 1,
        is_docked: false,
        is_landed: true,
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
    return Promise.resolve({ data: { sector_id: 1, name: 'Home' } });
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

describe('GameContext citadelAPI defense buildings', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockGetAvailableBuildings.mockResolvedValue({
      success: true,
      buildings: [{ type: 'turret_network', available: true }],
    });
    mockConstructBuilding.mockResolvedValue({ success: true, buildingType: 'turret_network' });
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
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('getDefenseBuildings routes through citadelAPI.getAvailableBuildings', async () => {
    let result: unknown;
    await act(async () => {
      result = await captured!.getDefenseBuildings('planet-9');
      await flush();
    });
    expect(mockGetAvailableBuildings).toHaveBeenCalledWith('planet-9');
    expect(result).toEqual({
      success: true,
      buildings: [{ type: 'turret_network', available: true }],
    });
    const rawGets = mockGet.mock.calls.filter((c) =>
      String(c[0]).includes('/buildings/available'),
    );
    expect(rawGets).toHaveLength(0);
  });

  it('buildDefenseBuilding routes through citadelAPI.constructBuilding', async () => {
    let result: unknown;
    await act(async () => {
      result = await captured!.buildDefenseBuilding('planet-9', 'turret_network');
      await flush();
    });
    expect(mockConstructBuilding).toHaveBeenCalledWith('planet-9', 'turret_network');
    expect(result).toEqual({ success: true, buildingType: 'turret_network' });
    const rawPosts = mockPost.mock.calls.filter((c) =>
      String(c[0]).includes('/buildings/construct') || String(c[0]).includes('/grid/place'),
    );
    expect(rawPosts).toHaveLength(0);
  });
});
