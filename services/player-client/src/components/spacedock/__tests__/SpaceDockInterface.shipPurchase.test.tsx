// @vitest-environment jsdom
/**
 * SpaceDockInterface — shipyard purchase money path (WO-TESTCOV-PLAYER-SHIPYARD-PURCHASE).
 * Hub → Shipyard → catalog GET → Purchase → Confirm → POST /api/v1/ships/purchase.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: false }),
}));

vi.mock('../../ships', () => ({
  ModuleGridInterface: () => <div data-testid="module-grid">modules</div>,
}));

const STATION = {
  id: 'station-1',
  name: 'Trading Post',
  type: 'TRADING',
  sector_id: 100,
  services: {
    ship_dealer: true,
    ship_repair: true,
    ship_maintenance: true,
    genesis_dealer: true,
    drone_shop: true,
    mine_dealer: true,
  },
  status: 'OPERATIONAL',
};

const PLAYER = {
  id: 'player-1',
  credits: 50_000,
  current_port_id: 'station-1',
  is_docked: true,
  attack_drones: 0,
  defense_drones: 0,
};

const updatePlayerCredits = vi.fn();
const refreshPlayerState = vi.fn().mockResolvedValue(undefined);
const loadShips = vi.fn().mockResolvedValue(undefined);
const gameState = {
  playerState: PLAYER,
  stationsInSector: [STATION],
  marketInfo: null,
  getMarketInfo: vi.fn(),
  buyResource: vi.fn(),
  sellResource: vi.fn(),
  dockAtStation: vi.fn(),
  bumpDockOccupant: vi.fn(),
  currentShip: { id: 'ship-1', type: 'FREIGHTER', name: 'Old Boat' },
  isLoading: false,
  error: null,
  updatePlayerCredits,
  updateShipGenesis: vi.fn(),
  refreshPlayerState,
  loadShips,
  getStationSlips: vi.fn().mockResolvedValue(null),
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const CATALOG = {
  ships: [
    {
      type: 'SCOUT_SHIP',
      name: 'Scout',
      base_cost: 1000,
      purchasable: true,
      speed: 10,
      turn_cost: 1,
      max_cargo: 10,
      max_colonists: 0,
      max_drones: 2,
      max_shields: 5,
      hull_points: 50,
      attack_rating: 1,
      defense_rating: 1,
      max_genesis_devices: 0,
      description: 'fast',
    },
  ],
};

describe('SpaceDockInterface — shipyard purchase', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-test');
    updatePlayerCredits.mockReset();
    refreshPlayerState.mockClear();
    loadShips.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/v1/ships/catalog')) {
        return { ok: true, json: async () => CATALOG };
      }
      if (u.includes('/api/v1/ships/purchase')) {
        return {
          ok: true,
          json: async () => ({
            remaining_credits: 49_000,
            ship: { id: 'ship-new', name: 'Scout', type: 'SCOUT_SHIP' },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  it('posts ships/purchase after catalog Confirm Purchase', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });

    const yard = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Shipyard'),
    ) as HTMLElement;
    expect(yard).toBeTruthy();
    await act(async () => {
      yard.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/ships/catalog'))).toBe(
        true,
      );
    });
    await vi.waitFor(() => {
      expect(container.textContent).toContain('Scout');
    });

    await act(async () => {
      (container.querySelector('.buy-ship-btn') as HTMLButtonElement).click();
    });
    expect(container.querySelector('.ship-confirm-panel')).toBeTruthy();

    await act(async () => {
      (container.querySelector('.action-button.primary') as HTMLButtonElement).click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/ships/purchase')),
      ).toBe(true);
    });

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/ships/purchase'),
    )!;
    const [, init] = call;
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ ship_type: 'SCOUT_SHIP' });
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    await vi.waitFor(() => {
      expect(updatePlayerCredits).toHaveBeenCalledWith(49_000);
    });
  });
});
