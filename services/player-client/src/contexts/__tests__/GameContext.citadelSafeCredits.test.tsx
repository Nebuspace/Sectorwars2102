// @vitest-environment jsdom
/**
 * GameContext — citadelAPI.deposit / withdraw (WO-WIRE-CITADEL-SAFE-CREDITS).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost, mockDeposit, mockWithdraw } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDeposit: vi.fn(),
  mockWithdraw: vi.fn(),
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
      deposit: (...a: unknown[]) => mockDeposit(...a),
      withdraw: (...a: unknown[]) => mockWithdraw(...a),
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

describe('GameContext citadelAPI deposit/withdraw', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockDeposit.mockResolvedValue({ credits_deposited: 50, safe_balance: 50 });
    mockWithdraw.mockResolvedValue({ credits_withdrawn: 25, safe_balance: 25 });
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

  it('depositToSafe routes through citadelAPI.deposit', async () => {
    let result: unknown;
    await act(async () => {
      result = await captured!.depositToSafe('planet-9', 50);
      await flush();
    });
    expect(mockDeposit).toHaveBeenCalledWith('planet-9', 50);
    expect(result).toEqual({ credits_deposited: 50, safe_balance: 50 });
    const rawPosts = mockPost.mock.calls.filter((c) =>
      String(c[0]).includes('/citadel/deposit'),
    );
    expect(rawPosts).toHaveLength(0);
  });

  it('withdrawFromSafe routes through citadelAPI.withdraw', async () => {
    let result: unknown;
    await act(async () => {
      result = await captured!.withdrawFromSafe('planet-9', 25);
      await flush();
    });
    expect(mockWithdraw).toHaveBeenCalledWith('planet-9', 25);
    expect(result).toEqual({ credits_withdrawn: 25, safe_balance: 25 });
    const rawPosts = mockPost.mock.calls.filter((c) =>
      String(c[0]).includes('/citadel/withdraw'),
    );
    expect(rawPosts).toHaveLength(0);
  });
});
