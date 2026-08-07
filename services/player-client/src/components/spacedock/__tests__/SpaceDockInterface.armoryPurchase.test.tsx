// @vitest-environment jsdom
/**
 * SpaceDockInterface — armory purchase money path (WO-TESTCOV-PLAYER-ARMORY).
 *
 * Hub → Armory → catalog GET → Buy → POST /api/v1/armory/purchase.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: false }),
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
  credits: 5000,
  current_port_id: 'station-1',
  is_docked: true,
  attack_drones: 0,
  defense_drones: 0,
};

const updatePlayerCredits = vi.fn();
const refreshPlayerState = vi.fn().mockResolvedValue(undefined);
const gameState = {
  playerState: PLAYER,
  stationsInSector: [STATION],
  marketInfo: null,
  getMarketInfo: vi.fn(),
  buyResource: vi.fn(),
  sellResource: vi.fn(),
  dockAtStation: vi.fn(),
  bumpDockOccupant: vi.fn(),
  currentShip: null,
  isLoading: false,
  error: null,
  updatePlayerCredits,
  updateShipGenesis: vi.fn(),
  refreshPlayerState,
  loadShips: vi.fn(),
  getStationSlips: vi.fn().mockResolvedValue(null),
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const CATALOG = {
  items: [
    {
      item: 'attack_drone',
      name: 'Attack Drone',
      price: 500,
      description: 'Offensive drone',
      service: 'drone_shop',
      available: true,
    },
  ],
  loadout: {
    attack_drones: 0,
    defense_drones: 0,
    mines: 0,
    caps: { attack_drones: 10, defense_drones: 10, mines: 5 },
  },
};

describe('SpaceDockInterface — armory purchase', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-test');
    updatePlayerCredits.mockReset();
    refreshPlayerState.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/v1/armory/catalog')) {
        return { ok: true, json: async () => CATALOG };
      }
      if (u.includes('/api/v1/armory/purchase')) {
        return {
          ok: true,
          json: async () => ({
            remaining_credits: 4500,
            loadout: {
              ...CATALOG.loadout,
              attack_drones: 1,
            },
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

  it('posts armory purchase when Buy is clicked', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });

    const armory = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Armory'),
    ) as HTMLElement;
    expect(armory).toBeTruthy();
    await act(async () => {
      armory.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/armory/catalog'))).toBe(
        true,
      );
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain('Attack Drone');
    });

    const buy = container.querySelector('button.buy-btn') as HTMLButtonElement;
    expect(buy).toBeTruthy();
    await act(async () => {
      buy.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/armory/purchase')),
      ).toBe(true);
    });

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/armory/purchase'),
    )!;
    const [url, init] = call;
    expect(String(url)).toMatch(/\/api\/v1\/armory\/purchase/);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ item: 'attack_drone', quantity: 1 });
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');

    await vi.waitFor(() => {
      expect(updatePlayerCredits).toHaveBeenCalledWith(4500);
    });
  });
});
