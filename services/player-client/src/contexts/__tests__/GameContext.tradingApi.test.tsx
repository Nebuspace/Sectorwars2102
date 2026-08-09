// @vitest-environment jsdom
/**
 * GameContext — tradingAPI dock/market/buy/sell (WO-WIRE-TRADING-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockDock,
  mockUndock,
  mockGetSlips,
  mockBumpSlip,
  mockGetMarket,
  mockBuy,
  mockSell,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDock: vi.fn(),
  mockUndock: vi.fn(),
  mockGetSlips: vi.fn(),
  mockBumpSlip: vi.fn(),
  mockGetMarket: vi.fn(),
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
    tradingAPI: {
      ...actual.tradingAPI,
      dock: (...a: unknown[]) => mockDock(...a),
      undock: (...a: unknown[]) => mockUndock(...a),
      getSlips: (...a: unknown[]) => mockGetSlips(...a),
      bumpSlip: (...a: unknown[]) => mockBumpSlip(...a),
      getMarket: (...a: unknown[]) => mockGetMarket(...a),
      buy: (...a: unknown[]) => mockBuy(...a),
      sell: (...a: unknown[]) => mockSell(...a),
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

describe('GameContext tradingAPI', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    mockGet.mockImplementation(defaultGet);
    mockPost.mockResolvedValue({ data: {} });
    mockDock.mockResolvedValue({ success: true });
    mockUndock.mockResolvedValue({ success: true });
    mockGetSlips.mockResolvedValue({ available: 1, capacity: 2 });
    mockBumpSlip.mockResolvedValue({ success: true });
    mockGetMarket.mockResolvedValue({ commodities: [] });
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
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('routes trading helpers through tradingAPI (no raw /trading traffic)', async () => {
    await act(async () => {
      await captured!.dockAtStation('st-1');
      await captured!.getStationSlips('st-1');
      await captured!.bumpDockOccupant('st-1', 'occ-1');
      await captured!.undockFromStation();
      await captured!.getMarketInfo('st-1');
      await captured!.buyResource('st-1', 'fuel', 2);
      await captured!.sellResource('st-1', 'organics', 1);
      await flush();
    });

    expect(mockDock).toHaveBeenCalledWith('st-1');
    expect(mockGetSlips).toHaveBeenCalledWith('st-1');
    expect(mockBumpSlip).toHaveBeenCalledWith('st-1', 'occ-1');
    expect(mockUndock).toHaveBeenCalled();
    expect(mockGetMarket).toHaveBeenCalledWith('st-1');
    expect(mockBuy).toHaveBeenCalledWith('st-1', 'fuel', 2);
    expect(mockSell).toHaveBeenCalledWith('st-1', 'organics', 1);

    const rawTrading = [...mockGet.mock.calls, ...mockPost.mock.calls].filter((c) =>
      String(c[0]).includes('/trading/'),
    );
    expect(rawTrading).toHaveLength(0);
  });
});
