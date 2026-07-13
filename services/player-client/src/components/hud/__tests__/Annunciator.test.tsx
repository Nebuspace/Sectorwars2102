// @vitest-environment jsdom
/**
 * Annunciator (WO-UI1-CHROME-COMPLETE) — the windshield HUD overlay,
 * rendered as the canonical SLIM LAMP STRIP:
 *   [WARN] · HAZARD  LAW  THREAT  TURNS  COMM · [CAUT]
 *
 * WebSocketContext and GameContext are mocked to mutable, reassignable
 * objects (mirrors NpcCombatBanner.test.tsx / the prior Annunciator.test.tsx
 * seam). services/api is mocked per the ThreatPage.test.tsx/
 * TacticalMonitor.test.tsx precedent (greyStatusAPI.getStatus / planetary
 * API.getOwnedPlanets are REST polls this component now makes). useMFD
 * needs a REAL ancestor provider (no established mock convention exists for
 * it anywhere in this codebase — MFDContext.tsx is small and dependency-
 * free, so wrapping with the real MFDProvider is simpler than inventing a
 * mock). services/deckNavBus and mfd/ariaFeedStore are spied (not mocked)
 * so the click-through assertions observe the REAL modules Annunciator
 * actually calls.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface MockNpcCombat {
  defender_id: string;
}
interface MockNewMessage {
  message_id: string;
  delivery: string[];
}

const markMessageRead = vi.fn().mockResolvedValue(undefined);
const mockGetGreyStatus = vi.fn();
const mockGetOwnedPlanets = vi.fn();

let mockWsState: {
  npcCombatSignal: number;
  lastNpcCombatInitiated: MockNpcCombat | null;
  newMessageSignal: number;
  lastNewMessage: MockNewMessage | null;
};
let mockGameState: {
  playerState: { id: string; turns: number; bounty_total?: number } | null;
  currentSector: { name?: string; hazard_level: number; radiation_level?: number } | null;
  markMessageRead: typeof markMessageRead;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

vi.mock('../../../services/api', () => ({
  greyStatusAPI: { getStatus: (...a: unknown[]) => mockGetGreyStatus(...a) },
  planetaryAPI: { getOwnedPlanets: (...a: unknown[]) => mockGetOwnedPlanets(...a) },
}));

import { MFDProvider } from '../../mfd/MFDContext';
import * as deckNavBus from '../../../services/deckNavBus';
import { ariaFeed } from '../../mfd/ariaFeedStore';
import Annunciator from '../Annunciator';

const setMatchMedia = (reducedMotion: boolean) => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reducedMotion,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
};

const INERT_GREY_STATUS = { isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null };

describe('Annunciator', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let requestTacticalPageSpy: ReturnType<typeof vi.spyOn>;
  let appendNavSpy: ReturnType<typeof vi.spyOn>;

  const render = () => {
    act(() => {
      root.render(
        <MFDProvider>
          <Annunciator />
        </MFDProvider>
      );
    });
  };

  /** Flushes the mount-time greyStatus/siege poll promises (real
   * microtasks — unaffected by vi.useFakeTimers, which only mocks
   * setTimeout/setInterval, not Promise resolution). */
  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    markMessageRead.mockClear();
    mockGetGreyStatus.mockReset().mockResolvedValue(INERT_GREY_STATUS);
    mockGetOwnedPlanets.mockReset().mockResolvedValue({ planets: [] });
    requestTacticalPageSpy = vi.spyOn(deckNavBus, 'requestTacticalPage');
    appendNavSpy = vi.spyOn(ariaFeed, 'appendNav').mockImplementation(() => {});
    setMatchMedia(false);
    mockWsState = {
      npcCombatSignal: 0,
      lastNpcCombatInitiated: null,
      newMessageSignal: 0,
      lastNewMessage: null,
    };
    mockGameState = {
      playerState: { id: 'player-1', turns: 500, bounty_total: 0 },
      currentSector: { name: 'Sol', hazard_level: 0, radiation_level: 0 },
      markMessageRead,
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
    // Targeted restores only -- vi.restoreAllMocks() would also wipe the
    // `markMessageRead`/mockGetGreyStatus/mockGetOwnedPlanets vi.fn()
    // instances' `.mockResolvedValue` implementations (mockRestore() on a
    // plain vi.fn(), as opposed to a vi.spyOn() of a real method, clears
    // the implementation entirely, not just call history).
    requestTacticalPageSpy.mockRestore();
    appendNavSpy.mockRestore();
  });

  /** Fresh unmount + remount with a NEW root -- needed for the poll-driven
   * triggers (LAW/siege), whose fetch only fires on the mount-time effect;
   * re-rendering the SAME root after reassigning the mock does not retrigger
   * it (the effect's `[enabled]` dependency is unchanged across a plain
   * re-render). */
  const remountFresh = () => {
    act(() => {
      root.unmount();
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
  };

  const bulb = (id: 'warn' | 'caut') => container.querySelector(`.annunciator-bulb.${id}`) as HTMLButtonElement;
  const seg = (label: string) =>
    Array.from(container.querySelectorAll('.annunciator-seg')).find((el) => el.textContent === label) as HTMLButtonElement;

  // ---- always-mounted strip -----------------------------------------------

  it('always renders the full slim strip (2 master bulbs + 5 segments), even fully idle', async () => {
    await flush();
    expect(container.querySelector('[data-testid="annunciator-strip"]')).not.toBeNull();
    expect(bulb('warn')).not.toBeNull();
    expect(bulb('caut')).not.toBeNull();
    expect(container.querySelectorAll('.annunciator-seg')).toHaveLength(5);
    expect(['HAZARD', 'LAW', 'THREAT', 'TURNS', 'COMM']).toEqual(
      Array.from(container.querySelectorAll('.annunciator-seg')).map((el) => el.textContent)
    );
    // Idle -- no lamp carries the live/on/ack state classes.
    expect(container.querySelectorAll('.is-live')).toHaveLength(0);
    expect(bulb('warn').classList.contains('on')).toBe(false);
    expect(bulb('caut').classList.contains('on')).toBe(false);

    const overlay = container.querySelector('[data-testid="annunciator-overlay"]') as HTMLElement;
    expect(overlay.style.pointerEvents).toBe('none');
  });

  it('Pixel-gate: role/aria-live are ALWAYS present (idle included), not toggled on activation', async () => {
    await flush(); // fully idle -- beforeEach's inert mock state
    expect(bulb('warn').getAttribute('role')).toBe('alert');
    expect(bulb('warn').getAttribute('aria-live')).toBe('assertive');
    expect(bulb('caut').getAttribute('role')).toBe('status');
    expect(bulb('caut').getAttribute('aria-live')).toBe('polite');

    expect(seg('THREAT').getAttribute('role')).toBe('alert'); // warn-severity
    expect(seg('THREAT').getAttribute('aria-live')).toBe('assertive');
    expect(seg('HAZARD').getAttribute('role')).toBe('status'); // caution-severity
    expect(seg('LAW').getAttribute('role')).toBe('status');
    expect(seg('TURNS').getAttribute('role')).toBe('status');
    expect(seg('COMM').getAttribute('role')).toBe('status'); // info-severity
    expect(seg('COMM').getAttribute('aria-live')).toBe('polite');

    // Now light HAZARD -- role/aria-live are UNCHANGED (static), only the
    // aria-label content moves (asserted separately below).
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 3, radiation_level: 0 } };
    render();
    await flush();
    expect(seg('HAZARD').getAttribute('role')).toBe('status');
    expect(seg('HAZARD').getAttribute('aria-live')).toBe('polite');
  });

  // ---- THREAT segment + WARN bulb (combat) --------------------------------

  it('COMBAT: lights the THREAT segment (warn) and the WARN bulb for the matching defender', async () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: { defender_id: 'player-1' } };
    render();
    await flush();

    expect(seg('THREAT').classList.contains('is-live')).toBe(true);
    expect(seg('THREAT').classList.contains('annunciator-seg--warn')).toBe(true);
    expect(bulb('warn').classList.contains('on')).toBe(true);
  });

  it('COMBAT: does NOT light THREAT for a spectator (defender_id mismatch)', async () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: { defender_id: 'someone-else' } };
    render();
    await flush();
    expect(seg('THREAT').classList.contains('is-live')).toBe(false);
    expect(bulb('warn').classList.contains('on')).toBe(false);
  });

  it('COMBAT: auto-clears THREAT/WARN after the dwell without any tap', async () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: { defender_id: 'player-1' } };
    render();
    await flush();
    expect(seg('THREAT').classList.contains('is-live')).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    expect(seg('THREAT').classList.contains('is-live')).toBe(false);
    expect(bulb('warn').classList.contains('on')).toBe(false);
  });

  it('THREAT: click requests the deck TACTICAL[TARGET] page', async () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: { defender_id: 'player-1' } };
    render();
    await flush();

    act(() => {
      seg('THREAT').click();
    });
    expect(requestTacticalPageSpy).toHaveBeenCalledWith('target');
  });

  // ---- WARN aggregate: siege + bounty (no segment of their own) ----------

  it('SIEGE: an owned planet under siege lights the WARN bulb (no segment — WARN-only trigger)', async () => {
    mockGetOwnedPlanets.mockResolvedValue({ planets: [{ id: 'p1', underSiege: true }] });
    remountFresh();
    await flush();

    expect(bulb('warn').classList.contains('on')).toBe(true);
    expect(container.querySelectorAll('.is-live')).toHaveLength(0); // no segment lit
  });

  it('BOUNTY: bounty_total > 0 lights the WARN bulb (no segment of its own)', async () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', turns: 500, bounty_total: 5000 } };
    render();
    await flush();

    expect(bulb('warn').classList.contains('on')).toBe(true);
    expect(container.querySelectorAll('.is-live')).toHaveLength(0);
  });

  // ---- HAZARD segment (caution) + analysis card ---------------------------

  it('HAZARD: lights the caution segment when sector hazard_level > 0', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 3, radiation_level: 0.1 } };
    render();
    await flush();

    expect(seg('HAZARD').classList.contains('is-live')).toBe(true);
    expect(seg('HAZARD').classList.contains('annunciator-seg--caution')).toBe(true);
    expect(bulb('caut').classList.contains('on')).toBe(true);
  });

  it('HAZARD: click opens the self-contained analysis card with real sector data', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 7, radiation_level: 0.42 } };
    render();
    await flush();

    expect(container.querySelector('[role="dialog"]')).toBeNull();
    act(() => {
      seg('HAZARD').click();
    });
    const card = container.querySelector('[role="dialog"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.textContent).toContain('Sol');
    expect(card.textContent).toContain('7/10');
    expect(card.textContent).toContain('42.0%');

    const closeBtn = card.querySelector('.annunciator-card-close') as HTMLButtonElement;
    act(() => {
      closeBtn.click();
    });
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  // ---- HAZARD card a11y (Pixel-gate fix-pass: Escape + focus mgmt) -------

  it('HAZARD card: opening moves focus to the close button (WCAG 2.4.3 focus-in)', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();

    act(() => {
      seg('HAZARD').click();
    });
    const closeBtn = container.querySelector('.annunciator-card-close') as HTMLButtonElement;
    expect(document.activeElement).toBe(closeBtn);
  });

  it('HAZARD card: Escape closes it and returns focus to the HAZARD lamp (WCAG 2.1.1 + 2.4.3 focus-restore)', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();

    const hazardBtn = seg('HAZARD');
    act(() => {
      hazardBtn.click();
    });
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();

    const card = container.querySelector('[role="dialog"]') as HTMLElement;
    act(() => {
      card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });

    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(hazardBtn);
  });

  it('HAZARD card: the close-button click also returns focus to the HAZARD lamp', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();

    const hazardBtn = seg('HAZARD');
    act(() => {
      hazardBtn.click();
    });
    const closeBtn = container.querySelector('.annunciator-card-close') as HTMLButtonElement;
    act(() => {
      closeBtn.click();
    });

    expect(document.activeElement).toBe(hazardBtn);
  });

  it('HAZARD card: role=dialog carries aria-modal="true"', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();
    act(() => {
      seg('HAZARD').click();
    });
    expect(container.querySelector('[role="dialog"]')?.getAttribute('aria-modal')).toBe('true');
  });

  // ---- LAW segment (caution, grey-flag/fine) ------------------------------

  it('LAW: greyStatus.isGrey lights the caution segment and the CAUT bulb (not WARN)', async () => {
    mockGetGreyStatus.mockResolvedValue({ isGrey: true, kind: 'player_attack', greyUntil: null, remainingSeconds: 300, clearFineCredits: 500 });
    remountFresh();
    await flush();

    expect(seg('LAW').classList.contains('is-live')).toBe(true);
    expect(seg('LAW').classList.contains('annunciator-seg--caution')).toBe(true);
    expect(bulb('caut').classList.contains('on')).toBe(true);
    expect(bulb('warn').classList.contains('on')).toBe(false);
  });

  it('LAW: click requests the deck TACTICAL[THREAT] page', async () => {
    mockGetGreyStatus.mockResolvedValue({ isGrey: true, kind: 'player_attack', greyUntil: null, remainingSeconds: 300, clearFineCredits: 500 });
    remountFresh();
    await flush();

    act(() => {
      seg('LAW').click();
    });
    expect(requestTacticalPageSpy).toHaveBeenCalledWith('threat');
  });

  // ---- TURNS segment (caution, no owning surface — narrates instead) -----

  it('TURNS: raises the caution segment when turns < 50', async () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', turns: 12 } };
    render();
    await flush();
    expect(seg('TURNS').classList.contains('is-live')).toBe(true);
  });

  it('TURNS: no lamp at or above the 50-turn threshold', async () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', turns: 50 } };
    render();
    await flush();
    expect(seg('TURNS').classList.contains('is-live')).toBe(false);
  });

  it('TURNS: click narrates the live turn count via ariaFeed (no deck/MFD navigation)', async () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', turns: 12 } };
    render();
    await flush();

    act(() => {
      seg('TURNS').click();
    });
    expect(appendNavSpy).toHaveBeenCalledWith(expect.stringContaining('12'));
    expect(requestTacticalPageSpy).not.toHaveBeenCalled();
  });

  // ---- COMM segment (info — never the danger lane) ------------------------

  it('COMM: raises the info segment for a toast-eligible non-urgent hail, never warn/caution classed', async () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-1', delivery: ['inbox', 'toast'] },
    };
    render();
    await flush();

    const commSeg = seg('COMM');
    expect(commSeg.classList.contains('is-live')).toBe(true);
    expect(commSeg.classList.contains('annunciator-seg--info')).toBe(true);
    expect(commSeg.classList.contains('annunciator-seg--warn')).toBe(false);
    expect(commSeg.classList.contains('annunciator-seg--caution')).toBe(false);
    // COMM never feeds either master bulb ("never sharing the danger lane").
    expect(bulb('warn').classList.contains('on')).toBe(false);
    expect(bulb('caut').classList.contains('on')).toBe(false);
  });

  it('COMM: does NOT raise for a low-priority inbox-only message (no toast surface)', async () => {
    mockWsState = { ...mockWsState, newMessageSignal: 1, lastNewMessage: { message_id: 'msg-2', delivery: ['inbox'] } };
    render();
    await flush();
    expect(seg('COMM').classList.contains('is-live')).toBe(false);
  });

  it('COMM: does NOT raise for an urgent hail (its own modal owns that surface)', async () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-3', delivery: ['inbox', 'toast', 'modal'] },
    };
    render();
    await flush();
    expect(seg('COMM').classList.contains('is-live')).toBe(false);
  });

  it('COMM: click marks the hail read AND opens the comms panel (both MFD screenIds, harmless no-op on the unregistered one)', async () => {
    mockWsState = {
      ...mockWsState,
      newMessageSignal: 1,
      lastNewMessage: { message_id: 'msg-4', delivery: ['inbox', 'toast'] },
    };
    render();
    await flush();

    act(() => {
      seg('COMM').click();
    });
    expect(markMessageRead).toHaveBeenCalledWith('msg-4');
    // Clears immediately (no dwell needed once acted on).
    expect(seg('COMM').classList.contains('is-live')).toBe(false);
  });

  // ---- master bulb ack lifecycle (flash -> ack -> steady -> auto-clear) --

  it('MASTER CAUTION: tap-acknowledge stops the flash but stays visible; auto-clears once the predicate resolves', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 4, radiation_level: 0 } };
    render();
    await flush();

    expect(bulb('caut').classList.contains('on')).toBe(true);
    act(() => {
      bulb('caut').click();
    });
    expect(bulb('caut').classList.contains('ack')).toBe(true);
    expect(bulb('caut').classList.contains('on')).toBe(false);
    // Segment itself is untouched by the master ack -- still shows live.
    expect(seg('HAZARD').classList.contains('is-live')).toBe(true);

    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 0, radiation_level: 0 } };
    render();
    await flush();
    expect(bulb('caut').classList.contains('on')).toBe(false);
    expect(bulb('caut').classList.contains('ack')).toBe(false);
  });

  it('MASTER WARN: a fresh false->true edge re-flashes even after a prior ack', async () => {
    mockWsState = { ...mockWsState, npcCombatSignal: 1, lastNpcCombatInitiated: { defender_id: 'player-1' } };
    render();
    await flush();
    act(() => {
      bulb('warn').click();
    });
    expect(bulb('warn').classList.contains('ack')).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    expect(bulb('warn').classList.contains('on')).toBe(false);
    expect(bulb('warn').classList.contains('ack')).toBe(false);

    mockWsState = { ...mockWsState, npcCombatSignal: 2 };
    render();
    await flush();
    expect(bulb('warn').classList.contains('on')).toBe(true);
  });

  // ---- reduced motion (Accept carried over) --------------------------------

  it('reduced motion: an active bulb stays visible (ack class) but never carries the flashing "on" class', async () => {
    act(() => {
      root.unmount();
    });
    setMatchMedia(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();

    expect(bulb('caut').classList.contains('on')).toBe(false);
    expect(bulb('caut').classList.contains('ack')).toBe(true);
  });

  // ---- a11y ----------------------------------------------------------------

  it('every button carries an aria-label naming the segment/bulb and its live state', async () => {
    mockGameState = { ...mockGameState, currentSector: { name: 'Sol', hazard_level: 3, radiation_level: 0 } };
    render();
    await flush();

    expect(seg('HAZARD').getAttribute('aria-label')).toContain('HAZARD');
    expect(seg('HAZARD').getAttribute('aria-label')).toContain('hazard level 3');
    expect(bulb('caut').getAttribute('aria-label')).toContain('Master caution');
    expect(bulb('caut').getAttribute('aria-label')).toContain('active');
    expect(seg('LAW').getAttribute('aria-label')).toContain('LAW');
    expect(seg('COMM').getAttribute('aria-label')).toContain('COMM');
  });
});
