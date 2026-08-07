// @vitest-environment jsdom
/**
 * SpaceDockInterface — blackjack deal money path (WO-TESTCOV-PLAYER-GAMBLING-BLACKJACK).
 * Hub → Gambling Hall → Stellar Blackjack → DEAL → POST /blackjack/deal.
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

describe('SpaceDockInterface — gambling blackjack deal', () => {
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
        player_cards: [
          { rank: '10', suit: '♠' },
          { rank: '8', suit: '♥' },
        ],
        dealer_cards: [
          { rank: 'K', suit: '♦' },
          { rank: '?', suit: '?' },
        ],
        player_total: 18,
        dealer_total: 10,
        game_over: false,
        result: null,
        can_double: true,
        deck_seed: 'seed-1',
        new_credits: 4900,
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

  it('posts blackjack/deal when DEAL CARDS is clicked', async () => {
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
      container.querySelector('.game-card.blackjack')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    expect(container.textContent).toMatch(/DEAL CARDS/);

    const deal = container.querySelector('.deal-button') as HTMLButtonElement;
    expect(deal.disabled).toBe(false);
    await act(async () => {
      deal.click();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes('/api/v1/gambling/blackjack/deal'),
        ),
      ).toBe(true);
    });

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/v1/gambling/blackjack/deal'),
    )!;
    const [, init] = call;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ bet_amount: 100 });
    expect(init.headers.Authorization).toBe('Bearer tok-test');
  });
});
