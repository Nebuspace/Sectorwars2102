// @vitest-environment jsdom
/**
 * GameContext — playerAPI / shipAPI.setActive (WO-WIRE-PLAYER-NAV-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockGetState,
  mockGetCurrentSector,
  mockGetShips,
  mockGetAvailableMoves,
  mockMove,
  mockScanLatentTunnels,
  mockGetCurrentShip,
  mockSetActive,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetState: vi.fn(),
  mockGetCurrentSector: vi.fn(),
  mockGetShips: vi.fn(),
  mockGetAvailableMoves: vi.fn(),
  mockMove: vi.fn(),
  mockScanLatentTunnels: vi.fn(),
  mockGetCurrentShip: vi.fn(),
  mockSetActive: vi.fn(),
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
    playerAPI: {
      getState: (...a: unknown[]) => mockGetState(...a),
      getCurrentSector: (...a: unknown[]) => mockGetCurrentSector(...a),
      getShips: (...a: unknown[]) => mockGetShips(...a),
      getAvailableMoves: (...a: unknown[]) => mockGetAvailableMoves(...a),
      move: (...a: unknown[]) => mockMove(...a),
      scanLatentTunnels: (...a: unknown[]) => mockScanLatentTunnels(...a),
    },
    shipAPI: {
      ...actual.shipAPI,
      getCurrentShip: (...a: unknown[]) => mockGetCurrentShip(...a),
      setActive: (...a: unknown[]) => mockSetActive(...a),
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
    quantumAPI: {
      ...actual.quantumAPI,
      getStatus: vi.fn().mockResolvedValue({
        quantum_charges: 0,
        quantum_shards: 0,
        charge_capacity: 0,
        refine_cooldown_ends_at: null,
      }),
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
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext playerAPI nav', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockGetState.mockResolvedValue({
      id: 'player-1',
      username: 'tester',
      credits: 1000,
      turns: 10,
      max_turns: 500,
      current_sector_id: 1,
      current_ship_id: 'ship-1',
      is_docked: false,
      is_landed: false,
      defense_drones: 0,
      attack_drones: 0,
      mines: 0,
      personal_reputation: 0,
      reputation_tier: 'unknown',
      name_color: '#fff',
      military_rank: 'Recruit',
    });
    mockGetCurrentShip.mockResolvedValue({ id: 'ship-1', type: 'SCOUT' });
    mockGetShips.mockResolvedValue([{ id: 'ship-1', type: 'SCOUT' }]);
    mockGetCurrentSector.mockResolvedValue({ sector_id: 1, name: 'Home' });
    mockGetAvailableMoves.mockResolvedValue({ moves: [2, 3] });
    mockMove.mockResolvedValue({ success: true });
    mockScanLatentTunnels.mockResolvedValue({ success: true, revealed: false });
    mockSetActive.mockResolvedValue({ success: true });
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

  it('routes nav helpers through playerAPI / shipAPI.setActive (no raw /player or set-active traffic)', async () => {
    mockGet.mockClear();
    mockPost.mockClear();

    await act(async () => {
      await captured!.refreshPlayerState();
      await captured!.loadShips();
      await captured!.setCurrentShip('ship-1');
      await captured!.moveToSector(2);
      await captured!.getAvailableMoves();
      await captured!.scanForLatentTunnels();
      await captured!.exploreCurrentLocation();
      await flush();
    });

    expect(mockGetState).toHaveBeenCalled();
    expect(mockGetCurrentShip).toHaveBeenCalled();
    expect(mockGetShips).toHaveBeenCalled();
    expect(mockSetActive).toHaveBeenCalledWith('ship-1');
    expect(mockMove).toHaveBeenCalledWith(2);
    expect(mockGetAvailableMoves).toHaveBeenCalled();
    expect(mockScanLatentTunnels).toHaveBeenCalled();
    expect(mockGetCurrentSector).toHaveBeenCalled();

    const rawPlayer = [...mockGet.mock.calls, ...mockPost.mock.calls].filter((c) => {
      const u = String(c[0]);
      return u.includes('/player/') || u.includes('/set-active');
    });
    expect(rawPlayer).toHaveLength(0);
  });
});
