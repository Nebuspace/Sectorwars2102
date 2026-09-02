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
  mockMove,
  mockScanLatentTunnels,
  mockDock,
  mockBuy,
  mockSell,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockGetState: vi.fn(),
  mockGetCurrentSector: vi.fn(),
  mockGetShips: vi.fn(),
  mockGetAvailableMoves: vi.fn(),
  mockGetCurrentShip: vi.fn(),
  mockSetActive: vi.fn(),
  mockMove: vi.fn(),
  mockScanLatentTunnels: vi.fn(),
  mockDock: vi.fn(),
  mockBuy: vi.fn(),
  mockSell: vi.fn(),
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
    tradingAPI: {
      ...actual.tradingAPI,
      dock: (...a: unknown[]) => mockDock(...a),
      buy: (...a: unknown[]) => mockBuy(...a),
      sell: (...a: unknown[]) => mockSell(...a),
      getMarket: vi.fn().mockResolvedValue({ resources: [] }),
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
  formatRefreshPlayerStateError,
  formatMoveToSectorError,
  formatScanLatentTunnelsError,
  formatScanAdjacentSectorError,
  formatDockAtStationError,
  formatBuyResourceError,
  formatSellResourceError,
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
    mockMove.mockResolvedValue({ success: true });
    mockScanLatentTunnels.mockResolvedValue({ success: true, revealed: false });
    mockDock.mockResolvedValue({ success: true });
    mockBuy.mockResolvedValue({ success: true });
    mockSell.mockResolvedValue({ success: true });
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

  it('refreshPlayerState TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM (LEG-3324)', async () => {
    mockGetState.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      await captured!.refreshPlayerState();
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to load player state');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });

  it('moveToSector TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM (LEG-3324)', async () => {
    mockMove.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      try {
        await captured!.moveToSector(2);
      } catch {
        /* expected throw */
      }
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to move to sector');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });

  it('scanForLatentTunnels TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM (LEG-3324)', async () => {
    mockScanLatentTunnels.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      try {
        await captured!.scanForLatentTunnels();
      } catch {
        /* expected throw */
      }
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to scan for latent tunnels');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });

  it('dockAtStation TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM (LEG-3324)', async () => {
    mockDock.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      try {
        await captured!.dockAtStation('station-1');
      } catch {
        /* expected throw */
      }
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to dock at port');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });

  it('buyResource TypeError surfaces stable fallback without Failed to fetch / TypeError in DOM (LEG-3324)', async () => {
    mockBuy.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      try {
        await captured!.buyResource('station-1', 'ore', 1);
      } catch {
        /* expected throw */
      }
      await flush();
    });

    const err = container.querySelector('[data-testid="game-error"]');
    expect(err?.textContent).toBe('Failed to buy resource');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });
});

describe('GameContext TypeError densify (LEG-3324)', () => {
  it('formatRefreshPlayerStateError falls back on TypeError network collapse', () => {
    const text = formatRefreshPlayerStateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load player state');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatMoveToSectorError falls back on TypeError network collapse', () => {
    const text = formatMoveToSectorError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to move to sector');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('formatScanLatentTunnelsError falls back on TypeError network collapse', () => {
    const text = formatScanLatentTunnelsError(new TypeError('Network Error'));
    expect(text).toBe('Failed to scan for latent tunnels');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatDockAtStationError falls back on TypeError network collapse', () => {
    const text = formatDockAtStationError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to dock at port');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('formatBuyResourceError falls back on TypeError network collapse', () => {
    const text = formatBuyResourceError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to buy resource');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('formatSellResourceError falls back on TypeError network collapse', () => {
    const text = formatSellResourceError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to sell resource');
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});

describe('GameContext Error Network Error densify (LEG-3401)', () => {
  it('formatSetActiveShipError falls back on Error Network Error', () => {
    const text = formatSetActiveShipError(new Error('Network Error'));
    expect(text).toBe('Failed to set active ship');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatGetAvailableMovesError falls back on Error Network Error', () => {
    const text = formatGetAvailableMovesError(new Error('Network Error'));
    expect(text).toBe('Failed to get available moves');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatRefreshPlayerStateError falls back on Error Network Error', () => {
    const text = formatRefreshPlayerStateError(new Error('Network Error'));
    expect(text).toBe('Failed to load player state');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatMoveToSectorError falls back on Error Network Error', () => {
    const text = formatMoveToSectorError(new Error('Network Error'));
    expect(text).toBe('Failed to move to sector');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatScanLatentTunnelsError falls back on Error Network Error', () => {
    const text = formatScanLatentTunnelsError(new Error('Network Error'));
    expect(text).toBe('Failed to scan for latent tunnels');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatScanAdjacentSectorError falls back on Error Network Error (LEG-3608)', () => {
    const text = formatScanAdjacentSectorError(new Error('Network Error'));
    expect(text).toBe('Failed to scan adjacent sector');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatDockAtStationError falls back on Error Network Error', () => {
    const text = formatDockAtStationError(new Error('Network Error'));
    expect(text).toBe('Failed to dock at port');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatBuyResourceError falls back on Error Network Error', () => {
    const text = formatBuyResourceError(new Error('Network Error'));
    expect(text).toBe('Failed to buy resource');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('formatSellResourceError falls back on Error Network Error', () => {
    const text = formatSellResourceError(new Error('Network Error'));
    expect(text).toBe('Failed to sell resource');
    expect(text).not.toMatch(/Network Error/i);
  });
});

describe('formatSetActiveShipError / formatGetAvailableMovesError 403/429 densify (LEG-4100)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatSetActiveShipError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatSetActiveShipError(apiRequestError(403, 'ship_denied'))).toBe('ship_denied');
    expect(formatSetActiveShipError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatSetActiveShipError(apiRequestError(429))).not.toMatch(/\b429\b/);

    expect(formatGetAvailableMovesError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatGetAvailableMovesError(apiRequestError(403, 'moves_denied'))).toBe('moves_denied');
    expect(formatGetAvailableMovesError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatGetAvailableMovesError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatGetAvailableMovesError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
