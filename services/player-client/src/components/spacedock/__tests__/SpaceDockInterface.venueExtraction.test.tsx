// @vitest-environment jsdom
/**
 * SpaceDockInterface — extracted venue smoke test (WO-UI3-VENUES sub-part #1).
 *
 * Proves the 7 formerly-inline `render*()` closures (Shipyard / Genesis /
 * Armory / Services / Mining / Gambling / Trading) now mount cleanly as
 * standalone `<Name>Venue />` components wired through SpaceDockInterface's
 * `renderActiveVenue()` switch, with the exact same prop values the inline
 * closures used to close over directly. This is the highest-fidelity smoke
 * test available: it exercises the REAL prop-threading written in the
 * switch (the actual risk surface of a pure-refactor extraction — a
 * mistyped prop name or wrong value mapping) rather than an isolated
 * shallow mount of each venue file with hand-picked props.
 *
 * Every venue is reached the same way a player reaches it: click its
 * `.venue-card` from the hub. Each assertion checks (a) no console.error
 * fired during mount/navigation and (b) the venue's own header text is
 * present, confirming the switch dispatched to the right component.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Trading Hub embeds the real TradingInterface, which pulls in
// useWebSocket() — mock it minimally (SpaceDockInterface's other venues
// don't touch WebSocketContext at all).
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addNotification: vi.fn(), isConnected: false }),
}));

// Every venue is advertised available by setting every gating service flag.
const STATION_1: any = {
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

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1',
      credits: 1000,
      current_port_id: 'station-1',
      is_docked: true,
      attack_drones: 0,
      defense_drones: 0,
    },
    stationsInSector: [STATION_1],
    marketInfo: null,
    getMarketInfo: vi.fn(),
    buyResource: vi.fn(),
    sellResource: vi.fn(),
    dockAtStation: vi.fn(),
    bumpDockOccupant: vi.fn(),
    currentShip: null,
    isLoading: false,
    error: null,
    updatePlayerCredits: vi.fn(),
    updateShipGenesis: vi.fn(),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    loadShips: vi.fn(),
    getStationSlips: vi.fn().mockResolvedValue(null),
    ...overrides,
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

const VENUES: Array<{ card: string; header: string }> = [
  { card: 'Trading Hub', header: '🏪 Trading Hub' },
  { card: 'Shipyard', header: '🛠️ Shipyard' },
  { card: 'Genesis Store', header: '🌍 Genesis Store' },
  { card: 'Armory', header: '⚔️ Armory' },
  { card: 'Ship Services', header: '🔧 Ship Services' },
  { card: 'Astral Mining', header: '⛏️ Astral Mining Consortium' },
  { card: 'Gambling Hall', header: '🎰 Gambling Hall' },
];

describe('SpaceDockInterface — extracted venues mount without error (WO-UI3-VENUES sub-part #1)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    gameState = makeGameState();
    localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
  });

  it.each(VENUES)('mounts $card cleanly with the correct header and zero console errors', async ({ card, header }) => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });

    const venueCard = Array.from(container.querySelectorAll('.venue-card'))
      .find(el => el.textContent?.includes(card)) as HTMLElement;
    expect(venueCard).toBeTruthy();

    await act(async () => {
      venueCard.click();
    });
    // Flush any pending microtasks from mount-time effects (fetch calls that
    // bail immediately since no accessToken is seeded).
    await act(async () => { await Promise.resolve(); });

    expect(container.querySelector('.spacedock-hub')).toBeNull();
    expect(container.textContent).toContain(header);
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
