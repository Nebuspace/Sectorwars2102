// @vitest-environment jsdom
/**
 * SpaceDockInterface — per-station-class hub greeter (WO-UI3-CONCIERGE).
 *
 * Replaces the old single static `.hub-welcome` line ("Welcome aboard...")
 * with 12 class-keyed static lines (STATION_GREETERS in
 * SpaceDockInterface.tsx), toned from sw2102-docs/FEATURES/economy/
 * haggling.md's port-personality archetype table + stationIdentity.tsx's
 * class blurbs. STATIC text only — no LLM, no `ai_dialogue_service` call,
 * no network request; the greeting is a pure function of `station_class`,
 * always available client-side via `getStationClassInfo`.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeStation(overrides: Record<string, unknown> = {}) {
  return {
    id: 'station-1',
    name: 'Trading Post',
    type: 'TRADING',
    sector_id: 100,
    services: {},
    status: 'OPERATIONAL',
    ...overrides,
  };
}

function makeGameState(station: unknown) {
  return {
    playerState: {
      id: 'player-1',
      credits: 1000,
      current_port_id: 'station-1',
      is_docked: true,
    },
    stationsInSector: [station],
    updatePlayerCredits: vi.fn(),
    updateShipGenesis: vi.fn(),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    loadShips: vi.fn(),
    getStationSlips: vi.fn().mockResolvedValue(null),
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

import SpaceDockInterface from '../SpaceDockInterface';

async function mountForClass(station_class: number | null) {
  gameState = makeGameState(makeStation({ station_class }));
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<SpaceDockInterface />);
  });
  return { container, root };
}

describe('SpaceDockInterface — per-station-class hub greeter (WO-UI3-CONCIERGE)', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let mounted: Array<{ container: HTMLElement; root: ReturnType<typeof createRoot> }> = [];

  beforeEach(() => {
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mounted = [];
  });

  afterEach(async () => {
    for (const { container, root } of mounted) {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    }
    errorSpy.mockRestore();
  });

  it('renders distinct static greeter text for at least 3 different station classes', async () => {
    const seen = new Set<string>();
    for (const stationClass of [0, 1, 8, 10]) {
      const { container, root } = await mountForClass(stationClass);
      mounted.push({ container, root });
      const greeter = container.querySelector('.hub-welcome p');
      expect(greeter).not.toBeNull();
      const text = greeter!.textContent || '';
      expect(text.length).toBeGreaterThan(0);
      // Not the old generic placeholder line.
      expect(text).not.toContain('Welcome aboard. Choose a destination');
      seen.add(text);
    }
    // All 4 sampled classes produced distinct copy.
    expect(seen.size).toBe(4);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('renders a coherent (non-empty) static greeter for every one of the 12 station classes', async () => {
    for (let stationClass = 0; stationClass <= 11; stationClass++) {
      const { container, root } = await mountForClass(stationClass);
      mounted.push({ container, root });
      const greeter = container.querySelector('.hub-welcome p');
      expect(greeter).not.toBeNull();
      expect((greeter!.textContent || '').trim().length).toBeGreaterThan(0);
    }
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('falls back to the generic line for an unrecognized/missing station class', async () => {
    const { container, root } = await mountForClass(null);
    mounted.push({ container, root });
    const greeter = container.querySelector('.hub-welcome p');
    expect(greeter).not.toBeNull();
    expect(greeter!.textContent).toContain('Welcome aboard. Choose a destination');
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('never contains an LLM/ARIA/dynamic-dialogue marker (STATIC-only guarantee)', async () => {
    for (const stationClass of [0, 4, 8, 9, 11]) {
      const { container, root } = await mountForClass(stationClass);
      mounted.push({ container, root });
      const greeter = container.querySelector('.hub-welcome p');
      const text = (greeter!.textContent || '').toLowerCase();
      expect(text).not.toMatch(/aria|loading trader|generating|\.\.\.$/);
    }
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
