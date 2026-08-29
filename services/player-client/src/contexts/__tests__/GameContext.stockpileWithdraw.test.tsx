// @vitest-environment jsdom
/**
 * GameContext — planetaryAPI.withdrawStockpileToCargo (LEG-546).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost, mockWithdrawStockpile } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockWithdrawStockpile: vi.fn(),
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
    planetaryAPI: {
      ...actual.planetaryAPI,
      withdrawStockpileToCargo: (...a: unknown[]) => mockWithdrawStockpile(...a),
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

describe('GameContext planetaryAPI stockpile withdraw', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockWithdrawStockpile.mockResolvedValue({
      success: true,
      message: 'Withdrew 10 fuel ore to cargo.',
      amount_to_cargo: 10,
      tax_skimmed: 0,
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
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('withdrawStockpileToCargo routes through planetaryAPI', async () => {
    let result: unknown;
    await act(async () => {
      result = await captured!.withdrawStockpileToCargo('planet-9', 'fuel_ore', 10);
      await flush();
    });
    expect(mockWithdrawStockpile).toHaveBeenCalledWith('planet-9', 'fuel_ore', 10);
    expect(result).toEqual({
      success: true,
      message: 'Withdrew 10 fuel ore to cargo.',
      amount_to_cargo: 10,
      tax_skimmed: 0,
    });
  });

  it('rethrows 403 non-owner detail from planetaryAPI', async () => {
    mockWithdrawStockpile.mockRejectedValueOnce(
      new Error('You do not own this planet and are not on the owner\'s team'),
    );
    await expect(
      captured!.withdrawStockpileToCargo('planet-9', 'organics', 5),
    ).rejects.toThrow('You do not own this planet and are not on the owner\'s team');
  });
});
