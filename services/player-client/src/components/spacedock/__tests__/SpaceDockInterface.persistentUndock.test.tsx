// @vitest-environment jsdom
/**
 * SpaceDockInterface — persistent UNDOCK across venue switches
 * (WO-UI3-STATION-MODE).
 *
 * Bug this proves fixed: the only UNDOCK button lived inside `renderHub()`
 * (`.hub-undock-btn`, SpaceDockInterface.tsx ~L1817-1828 pre-WO) — every
 * OTHER venue (shipyard/armory/services/mining/gambling/trading/genesis/
 * construction/portoffice/contracts) rendered none at all. A player who
 * navigated off the hub had no way to undock without first clicking back.
 *
 * Fix: a single `.station-face-undock` instance now renders in the OUTER
 * FRAME (sibling to `renderActiveVenue()`, SpaceDockInterface.tsx's final
 * `return`), so it survives every `activeVenue` switch. Deliberately NOT
 * de-duped against the hub's own `.hub-undock-btn` (see the WO's own
 * comment at the outer-frame call site) — renderHub()'s internals are
 * CONCIERGE's region, sequenced after this WO, so both instances coexist on
 * the hub view; this suite only asserts the persistent one is present in
 * BOTH states.
 *
 * Mount target: gambling (`renderGamblingHall`) — the one venue that is
 * ALWAYS available (no stationServices gate) and renders fully inline (no
 * external heavy child component / API-catalog fetch to mock), so this stays
 * a real, unmocked SpaceDockInterface render rather than a stubbed one.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STATION_1: any = {
  id: 'station-1',
  name: 'Trading Post',
  type: 'TRADING',
  sector_id: 100,
  services: {},
  status: 'OPERATIONAL',
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1',
      credits: 1000,
      current_port_id: 'station-1',
      is_docked: true,
    },
    stationsInSector: [STATION_1],
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

describe('SpaceDockInterface — persistent UNDOCK survives a venue switch (WO-UI3-STATION-MODE)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  const onUndock = vi.fn();

  beforeEach(() => {
    gameState = makeGameState();
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

  it('renders on the hub view (alongside the hub-only button)', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface onUndock={onUndock} helmBusy={false} />);
    });

    const persistent = container.querySelector('.station-face-undock');
    expect(persistent).not.toBeNull();
    expect(persistent?.textContent).toContain('UNDOCK & LAUNCH');
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('survives switching to a non-hub venue (gambling) — the hub-only button does not', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface onUndock={onUndock} helmBusy={false} />);
    });

    const gamblingCard = Array.from(container.querySelectorAll('.venue-card'))
      .find(el => el.textContent?.includes('Gambling Hall')) as HTMLElement;
    expect(gamblingCard).toBeTruthy();

    await act(async () => {
      gamblingCard.click();
    });

    // Now on the gambling venue — confirm the hub's own markup is gone
    // (the switch genuinely happened, not a no-op click).
    expect(container.querySelector('.spacedock-hub')).toBeNull();
    expect(container.textContent).toContain('Gambling Hall');

    // The persistent frame-level button is still there and still wired.
    const persistent = container.querySelector('.station-face-undock') as HTMLButtonElement;
    expect(persistent).not.toBeNull();
    await act(async () => {
      persistent.click();
    });
    expect(onUndock).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('respects helmBusy (disabled, no click-through) on the persistent button', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface onUndock={onUndock} helmBusy={true} />);
    });

    const persistent = container.querySelector('.station-face-undock') as HTMLButtonElement;
    expect(persistent).not.toBeNull();
    // Native `disabled` alone conveys the state (Pixel a11y REVISE — redundant
    // `aria-disabled` on a native <button> removed, WO-UI3-STATION-MODE).
    expect(persistent.disabled).toBe(true);
    expect(persistent.hasAttribute('aria-disabled')).toBe(false);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('omitted entirely when onUndock is not supplied (optional prop contract preserved)', async () => {
    await act(async () => {
      root.render(<SpaceDockInterface />);
    });

    expect(container.querySelector('.station-face-undock')).toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
