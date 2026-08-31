// @vitest-environment jsdom
/**
 * GameContext — planetary action TypeError densify (LEG-3320).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost, mockLand, mockClaimSettle } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockLand: vi.fn(),
  mockClaimSettle: vi.fn(),
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
      land: (...a: unknown[]) => mockLand(...a),
    },
    expeditionAPI: {
      ...actual.expeditionAPI,
      settle: (...a: unknown[]) => mockClaimSettle(...a),
    },
    playerAPI: {
      getState: vi.fn().mockResolvedValue({
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
      }),
      getCurrentSector: vi.fn().mockResolvedValue({ sector_id: 1, name: 'Home' }),
      getShips: vi.fn().mockResolvedValue([{ id: 'ship-1', type: 'SCOUT' }]),
      getAvailableMoves: vi.fn().mockResolvedValue({ moves: [2, 3] }),
    },
    shipAPI: {
      ...actual.shipAPI,
      getCurrentShip: vi.fn().mockResolvedValue({ id: 'ship-1', type: 'SCOUT' }),
    },
    sectorAPI: {
      ...actual.sectorAPI,
      getPlanets: vi.fn().mockResolvedValue({ planets: [] }),
      getStations: vi.fn().mockResolvedValue({ stations: [] }),
      explore: vi.fn().mockResolvedValue({}),
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
  formatPlanetaryActionError,
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

describe('GameContext planetary TypeError densify (LEG-3320)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockLand.mockResolvedValue({ success: true });
    mockClaimSettle.mockResolvedValue({ success: true });
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

  it('formatPlanetaryActionError falls back on TypeError network collapse', () => {
    const text = formatPlanetaryActionError(new TypeError('Failed to fetch'), 'Failed to land on planet');
    expect(text).toBe('Failed to land on planet');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatPlanetaryActionError falls back on axios Network Error / Failed to fetch', () => {
    expect(
      formatPlanetaryActionError(new Error('Network Error'), 'Failed to claim planet'),
    ).toBe('Failed to claim planet');
    expect(
      formatPlanetaryActionError(new Error('Failed to fetch'), 'Failed to leave planet'),
    ).toBe('Failed to leave planet');
  });

  it('formatPlanetaryActionError preserves structured API detail', () => {
    expect(
      formatPlanetaryActionError(
        { response: { data: { detail: 'Insufficient credits for claim' } } },
        'Failed to claim planet',
      ),
    ).toBe('Insufficient credits for claim');
  });

  it('landOnPlanet TypeError surfaces stable fallback without raw network tokens in DOM', async () => {
    mockLand.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      try {
        await captured!.landOnPlanet('planet-9');
      } catch {
        /* expected */
      }
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to land on planet');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('claimPlanet TypeError surfaces stable fallback without raw network tokens in DOM', async () => {
    mockClaimSettle.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      try {
        await captured!.claimPlanet('planet-1');
      } catch {
        /* expected */
      }
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to claim planet');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
