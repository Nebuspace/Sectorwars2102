// @vitest-environment jsdom
/**
 * SpaceDockInterface — lottery ticket money path (WO-TESTCOV-PLAYER-GAMBLING-LOTTERY).
 * Hub → Gambling Hall → Sector Lottery → pick 4 → BUY → POST buy-ticket.
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

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('SpaceDockInterface — gambling lottery ticket', () => {
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
        winning_numbers: [1, 2, 3, 9],
        matches: 3,
        net_result: 500,
        jackpot: false,
        new_credits: 5500,
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

  it('posts buy-ticket after selecting four sectors', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });

    const hall = Array.from(container.querySelectorAll('.venue-card')).find((el) =>
      el.textContent?.includes('Gambling Hall'),
    ) as HTMLElement;
    await act(async () => {
      hall.click();
    });

    await act(async () => {
      container.querySelector('.game-card.lottery')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });

    const picks = Array.from(container.querySelectorAll('.sector-pick')).slice(0, 4);
    expect(picks.length).toBe(4);
    for (const pick of picks) {
      await act(async () => {
        pick.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
    }

    const buy = container.querySelector('.buy-ticket-btn') as HTMLButtonElement;
    expect(buy.disabled).toBe(false);
    await act(async () => {
      buy.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes('/api/v1/gambling/lottery/buy-ticket'),
        ),
      ).toBe(true);
    });

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/gambling/lottery/buy-ticket'),
    )!;
    const [, init] = call;
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body.bet_amount).toBe(100);
    expect(body.numbers).toEqual([1, 2, 3, 4]);
    expect(init.headers.Authorization).toBe('Bearer tok-test');
    await vi.waitFor(() => {
      expect(updatePlayerCredits).toHaveBeenCalledWith(5500);
    });
  });
});
