// @vitest-environment jsdom
/**
 * GameContext — firstLoginAPI.getStatus (WO-WIRE-FIRST-LOGIN-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockGetStatus,
  mockGetState,
  mockGetShips,
  mockGetCurrentSector,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetStatus: vi.fn(),
  mockGetState: vi.fn(),
  mockGetShips: vi.fn(),
  mockGetCurrentSector: vi.fn(),
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
    firstLoginAPI: {
      getStatus: (...a: unknown[]) => mockGetStatus(...a),
    },
    playerAPI: {
      ...actual.playerAPI,
      getState: (...a: unknown[]) => mockGetState(...a),
      getShips: (...a: unknown[]) => mockGetShips(...a),
      getCurrentSector: (...a: unknown[]) => mockGetCurrentSector(...a),
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

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext firstLoginAPI', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockResolvedValue({ data: {} });
    mockPost.mockResolvedValue({ data: {} });
    mockGetStatus.mockResolvedValue({ requires_first_login: false });
    mockGetState.mockResolvedValue({
      id: 'player-1',
      username: 'tester',
      credits: 1000,
      turns: 10,
      max_turns: 500,
      current_sector_id: 1,
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
    mockGetShips.mockResolvedValue([]);
    mockGetCurrentSector.mockResolvedValue({ sector_id: 1, name: 'Home' });
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

  it('routes checkFirstLoginStatus through firstLoginAPI.getStatus (no raw /first-login traffic)', async () => {
    mockGet.mockClear();
    mockPost.mockClear();
    mockGetStatus.mockClear();
    mockGetStatus.mockResolvedValue({ requires_first_login: true });

    let result = false;
    await act(async () => {
      result = await captured!.checkFirstLoginStatus();
      await flush();
    });

    expect(result).toBe(true);
    expect(captured!.needsFirstLogin).toBe(true);
    expect(mockGetStatus).toHaveBeenCalled();

    const raw = [...mockGet.mock.calls, ...mockPost.mock.calls].filter((c) =>
      String(c[0]).includes('/first-login'),
    );
    expect(raw).toHaveLength(0);
  });
});
