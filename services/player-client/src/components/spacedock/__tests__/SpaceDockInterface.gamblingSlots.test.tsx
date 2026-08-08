// @vitest-environment jsdom
/**
 * SpaceDockInterface — gambling slots money path (WO-TESTCOV-PLAYER-SPACEDOCK-SHELL).
 *
 * Hub → Gambling Hall → Cosmic Slots → SPIN posts /api/v1/gambling/slots/spin.
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
  refreshPlayerState: vi.fn().mockResolvedValue(undefined),
  loadShips: vi.fn(),
  getStationSlips: vi.fn().mockResolvedValue(null),
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

describe('SpaceDockInterface — gambling slots spin', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-test');
    updatePlayerCredits.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        reels: ['💎', '💎', '💎'],
        win_amount: 500,
        is_jackpot: true,
        credits_remaining: 5500,
      }),
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

  it('posts a slots spin when Cosmic Slots SPIN is clicked', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await act(async () => {
        root.render(<SpaceDockInterface />);
      });

      const hall = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
        el.textContent?.includes('Gambling Hall'),
      ) as HTMLElement;
      expect(hall).toBeTruthy();
      await act(async () => {
        hall.click();
      });

      await act(async () => {
        container.querySelector('.game-card.slots')!.dispatchEvent(
          new MouseEvent('click', { bubbles: true }),
        );
      });
      expect(container.textContent).toMatch(/COSMIC SLOTS/);

      const spin = container.querySelector('button.spin-button') as HTMLButtonElement;
      expect(spin).toBeTruthy();
      await act(async () => {
        spin.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });

      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalled();
      });

      const call = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes('/api/v1/gambling/slots/spin'),
      );
      expect(call).toBeTruthy();
      const [url, init] = call!;
      expect(String(url)).toMatch(/\/api\/v1\/gambling\/slots\/spin/);
      expect(init.method).toBe('POST');
      expect(JSON.parse(init.body as string)).toEqual({ bet_amount: 100 });
      expect(init.headers.Authorization).toBe('Bearer tok-test');

      // Finish the 1.5s post-fetch animation so unmount doesn't leave timers
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1600);
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
