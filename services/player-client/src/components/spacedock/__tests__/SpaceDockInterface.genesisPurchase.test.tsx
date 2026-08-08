// @vitest-environment jsdom
/**
 * SpaceDockInterface — genesis device purchase money path
 * (WO-TESTCOV-PLAYER-GENESIS-PURCHASE). Complements genesisDevicePrice suite
 * which never clicks Acquire → POST.
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
  services: { genesis_dealer: true },
  status: 'OPERATIONAL',
};

const updatePlayerCredits = vi.fn();
const updateShipGenesis = vi.fn();
const gameState = {
  playerState: {
    id: 'player-1',
    credits: 100_000,
    current_port_id: 'station-1',
    is_docked: true,
  },
  stationsInSector: [STATION],
  marketInfo: null,
  getMarketInfo: vi.fn(),
  buyResource: vi.fn(),
  sellResource: vi.fn(),
  dockAtStation: vi.fn(),
  bumpDockOccupant: vi.fn(),
  currentShip: { id: 'ship-1', type: 'CARGO_HAULER', name: 'Hauler' },
  isLoading: false,
  error: null,
  updatePlayerCredits,
  updateShipGenesis,
  refreshPlayerState: vi.fn().mockResolvedValue(undefined),
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
  name: 'Hauler',
  type: 'CARGO_HAULER',
  genesis_devices: 0,
  max_genesis_devices: 2,
};

describe('SpaceDockInterface — genesis purchase', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-test');
    updatePlayerCredits.mockReset();
    updateShipGenesis.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/v1/player/current-ship')) {
        return { ok: true, json: async () => SHIP };
      }
      if (u.includes('/api/v1/genesis/available')) {
        return {
          ok: true,
          json: async () => ({
            device_acquisition_cost: 25_000,
            purchases_remaining: 3,
            max_purchases_per_week: 3,
            reputation_gate: { required: 250, current: 1000, met: true },
            tiers: {
              basic: { cost: 25_000 },
              enhanced: { cost: 75_000 },
              advanced: { cost: 250_000 },
            },
          }),
        };
      }
      if (u.includes('/api/v1/player/genesis/purchase')) {
        return {
          ok: true,
          json: async () => ({
            new_credits: 75_000,
            genesis_devices: 1,
            max_genesis_devices: 2,
            purchases_remaining: 2,
            weekly_limit: 3,
            message: 'Genesis Device acquired.',
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

  it('posts genesis/purchase when Acquire Device is clicked', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/v1/player/current-ship')),
      ).toBe(true);
    });

    const venue = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Genesis Store'),
    ) as HTMLElement;
    await act(async () => {
      venue.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.purchase-device-btn')).toBeTruthy();
    });

    const acquire = container.querySelector('.purchase-device-btn') as HTMLButtonElement;
    expect(acquire.disabled).toBe(false);
    expect(acquire.textContent).toContain('Acquire Device');

    await act(async () => {
      acquire.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes('/api/v1/player/genesis/purchase'),
        ),
      ).toBe(true);
    });

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/player/genesis/purchase'),
    )!;
    const [, init] = call;
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({});
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    await vi.waitFor(() => {
      expect(updatePlayerCredits).toHaveBeenCalledWith(75_000);
      expect(updateShipGenesis).toHaveBeenCalledWith(1);
    });
  });
});
