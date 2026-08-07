// @vitest-environment jsdom
/**
 * SpaceDockInterface — ship repair money path (WO-TESTCOV-PLAYER-SHIP-REPAIR).
 * Hub → Ship Services → Full Repair → POST /api/v1/player/ships/:id/repair.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: false }),
}));

vi.mock('../../ships', () => ({
  InsuranceManager: () => null,
  MaintenanceManager: () => null,
  ModuleGridInterface: () => null,
  TIER_LABEL: {},
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
const gameState = {
  playerState: PLAYER,
  stationsInSector: [STATION],
  marketInfo: null,
  getMarketInfo: vi.fn(),
  buyResource: vi.fn(),
  sellResource: vi.fn(),
  dockAtStation: vi.fn(),
  bumpDockOccupant: vi.fn(),
  currentShip: { id: 'ship-1', type: 'SCOUT_SHIP', name: 'Rusty Nail' },
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

const SHIP = {
  id: 'ship-1',
  name: 'Rusty Nail',
  type: 'SCOUT_SHIP',
  genesis_devices: 0,
  max_genesis_devices: 0,
  current_value: 10_000,
  cargo_capacity: 50,
  cargo: { used: 0 },
  combat: { hull: 50, max_hull: 100, shields: 25, max_shields: 50 },
};

describe('SpaceDockInterface — ship repair', () => {
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
      if (u.includes('/api/v1/player/current-ship')) {
        return { ok: true, json: async () => SHIP };
      }
      if (u.includes('/api/v1/player/ships/ship-1/repair')) {
        return {
          ok: true,
          json: async () => ({
            credits_remaining: 49_250,
            hull: 100,
            shields: 50,
            max_hull: 100,
            max_shields: 50,
            message: 'Hull and shields restored.',
          }),
        };
      }
      if (u.includes('/api/v1/ships/') && u.includes('/insurance')) {
        return { ok: true, json: async () => ({ current_tier: null }) };
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

  it('posts repair when Full Repair is clicked', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/player/current-ship')),
      ).toBe(true);
    });

    const services = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Ship Services'),
    ) as HTMLElement;
    expect(services).toBeTruthy();
    await act(async () => {
      services.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain('Full Repair');
    });

    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Full Repair'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);

    await act(async () => {
      btn.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes('/api/v1/player/ships/ship-1/repair'),
        ),
      ).toBe(true);
    });

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/player/ships/ship-1/repair'),
    )!;
    const [, init] = call;
    expect(init?.method).toBe('POST');
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    await vi.waitFor(() => {
      expect(updatePlayerCredits).toHaveBeenCalledWith(49_250);
    });
  });
});
