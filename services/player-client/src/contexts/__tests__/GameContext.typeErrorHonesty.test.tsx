// @vitest-environment jsdom
/**
 * GameContext — setActiveShip / getAvailableMoves TypeError densify (LEG-3265).
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
  mockGetCurrentShip,
  mockSetActive,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetState: vi.fn(),
  mockGetCurrentSector: vi.fn(),
  mockGetShips: vi.fn(),
  mockGetAvailableMoves: vi.fn(),
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

import {
  GameProvider,
  useGame,
  formatSetActiveShipError,
  formatGetAvailableMovesError,
} from '../GameContext';

function defaultGet(url: string) {
  if (url === '/api/v1/first-login/status') {
    return Promise.resolve({ data: { requires_first_login: false } });
  }
  return Promise.resolve({ data: {} });
}

let captured: ReturnType<typeof useGame> | null = null;
function Consumer() {
  captured = useGame();
  return captured.error ? <div data-testid="game-error">{captured.error}</div> : null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('GameContext TypeError densify (LEG-3265)', () => {
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

  it('formatSetActiveShipError falls back on TypeError network collapse', () => {
    const text = formatSetActiveShipError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to set active ship');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatGetAvailableMovesError falls back on TypeError network collapse', () => {
    const text = formatGetAvailableMovesError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to get available moves');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('setCurrentShip TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM', async () => {
    mockSetActive.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      await captured!.setCurrentShip('ship-2');
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to set active ship');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('getAvailableMoves TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM', async () => {
    mockGetAvailableMoves.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      await captured!.getAvailableMoves();
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to get available moves');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
