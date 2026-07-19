// @vitest-environment jsdom
/**
 * Annunciator (WO-HUD-LIGHTS phase 1) — the windshield HUD overlay,
 * rendered as the canonical SLIM LAMP STRIP:
 *   [ALERT] · HAZARD  LAW  THREAT  COMM
 *
 * WebSocketContext and GameContext are mocked to mutable, reassignable
 * objects (mirrors NpcCombatBanner.test.tsx / the prior Annunciator.test.tsx
 * seam). LAW/THREAT are now sourced from `currentSector.players_present`
 * (the API snapshot -- where NPC archetype/reputation enrichment actually
 * lives, per contactClassification.ts's own doc-comment) rather than a REST
 * poll. services/api IS still mocked, though -- restored by hub ruling:
 * siege (planetaryAPI.getOwnedPlanets, REST poll) is back as a master-only,
 * segment-less ALERT contributor, mirroring its pre-phase-1 shape. useMFD
 * needs a REAL ancestor provider (no established mock convention exists for
 * it anywhere in this codebase). services/deckNavBus is spied (not mocked)
 * so the click-through assertions observe the REAL module Annunciator
 * actually calls.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface MockContact {
  user_id?: string;
  player_id?: string;
  username?: string;
  is_npc?: boolean;
  archetype?: string;
  notoriety?: number;
  reputation_tier?: string;
}

let mockWsState: {
  sectorPlayers: MockContact[];
};
let mockGameState: {
  playerState: { id: string; username?: string; bounty_total?: number } | null;
  currentSector: { name?: string; hazard_level: number; radiation_level?: number; players_present?: MockContact[] } | null;
  unreadMessageCount: number;
};

const mockGetOwnedPlanets = vi.fn();

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

vi.mock('../../../services/api', () => ({
  planetaryAPI: { getOwnedPlanets: (...a: unknown[]) => mockGetOwnedPlanets(...a) },
}));

import { MFDProvider } from '../../mfd/MFDContext';
import * as deckNavBus from '../../../services/deckNavBus';
import Annunciator from '../Annunciator';

const setMatchMedia = (reducedMotion: boolean) => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reducedMotion,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
};

const LAW_CONTACT: MockContact = { player_id: 'npc-law-1', is_npc: true, archetype: 'LAW_ENFORCEMENT', username: 'Marshal Vex' };
const RAIDER_CONTACT: MockContact = { player_id: 'npc-raider-1', is_npc: true, archetype: 'HOSTILE_RAIDER', username: 'Raider' };
const GREY_PLAYER_CONTACT: MockContact = { user_id: 'player-2', is_npc: false, username: 'Shifty', reputation_tier: 'Suspicious' };
const CLEAR_CONTACT: MockContact = { player_id: 'npc-trader-1', is_npc: true, archetype: 'TRADER', username: 'Merchant', notoriety: 0 };

describe('Annunciator', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let requestTacticalPageSpy: ReturnType<typeof vi.spyOn>;

  const render = () => {
    act(() => {
      root.render(
        <MFDProvider>
          <Annunciator />
        </MFDProvider>
      );
    });
  };

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    requestTacticalPageSpy = vi.spyOn(deckNavBus, 'requestTacticalPage');
    mockGetOwnedPlanets.mockReset().mockResolvedValue({ planets: [] });
    setMatchMedia(false);
    mockWsState = { sectorPlayers: [] };
    mockGameState = {
      playerState: { id: 'player-1', username: 'commander', bounty_total: 0 },
      currentSector: { name: 'Sol', hazard_level: 0, radiation_level: 0, players_present: [] },
      unreadMessageCount: 0,
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
    requestTacticalPageSpy.mockRestore();
  });

  /** Fresh unmount + remount with a NEW root -- needed for the siege poll,
   * whose fetch only fires on the mount-time effect; re-rendering the SAME
   * root after reassigning the mock does not retrigger it (the effect's
   * `[enabled]` dependency is unchanged across a plain re-render). */
  const remountFresh = () => {
    act(() => {
      root.unmount();
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
  };

  const bulb = () => container.querySelector('.bulb') as HTMLButtonElement;
  const seg = (label: string) =>
    Array.from(container.querySelectorAll('.seg')).find((el) => el.textContent === label) as HTMLButtonElement;
  // Any of the 3 bare lit-classes cockpit-shell.css defines -- a segment is
  // "lit" iff it carries exactly one of these.
  const LIT_CLASSES = ['live', 'livec', 'livecm'];
  const litClassOf = (el: HTMLElement) => LIT_CLASSES.find((c) => el.classList.contains(c));

  // ---- always-mounted strip -----------------------------------------------

  it('always renders the full slim strip (1 master bulb + 4 segments), even fully idle', async () => {
    await flush();
    expect(container.querySelector('[data-testid="annunciator-strip"]')).not.toBeNull();
    expect(container.querySelectorAll('.bulb')).toHaveLength(1);
    expect(container.querySelectorAll('.seg')).toHaveLength(4);
    expect(['HAZARD', 'LAW', 'THREAT', 'COMM']).toEqual(
      Array.from(container.querySelectorAll('.seg')).map((el) => el.textContent)
    );
    // Idle -- no lamp carries a live/on/ack state class.
    expect(container.querySelectorAll('.live, .livec, .livecm')).toHaveLength(0);
    expect(bulb().classList.contains('on')).toBe(false);

    const overlay = container.querySelector('[data-testid="annunciator-overlay"]') as HTMLElement;
    expect(overlay.style.pointerEvents).toBe('none');
  });

  it('emits the bare artifact classnames, one lamp/bulb, one segs group of 4', async () => {
    await flush();
    const strip = container.querySelector('[data-testid="annunciator-strip"]') as HTMLElement;
    expect(strip.classList.contains('annun')).toBe(true);
    expect(container.querySelectorAll('.lamp')).toHaveLength(1);
    expect(container.querySelectorAll('.bulb')).toHaveLength(1);
    expect(container.querySelectorAll('.segs')).toHaveLength(1);
    expect(container.querySelectorAll('.seg')).toHaveLength(4);
    expect(container.querySelector('.annunciator-overlay')).not.toBeNull();
  });

  it('Pixel-gate: role/aria-live are ALWAYS present (idle included), not toggled on activation', async () => {
    await flush(); // fully idle -- beforeEach's inert mock state
    expect(bulb().getAttribute('role')).toBe('alert');
    expect(bulb().getAttribute('aria-live')).toBe('assertive');

    expect(seg('THREAT').getAttribute('role')).toBe('alert'); // warn-severity
    expect(seg('THREAT').getAttribute('aria-live')).toBe('assertive');
    expect(seg('HAZARD').getAttribute('role')).toBe('status'); // caution-severity
    expect(seg('LAW').getAttribute('role')).toBe('status'); // caution-severity
    expect(seg('COMM').getAttribute('role')).toBe('status'); // info-severity
    expect(seg('COMM').getAttribute('aria-live')).toBe('polite');

    // Now light HAZARD -- role/aria-live are UNCHANGED (static), only the
    // aria-label content moves.
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 7 } };
    render();
    await flush();
    expect(seg('HAZARD').getAttribute('role')).toBe('status');
    expect(seg('HAZARD').getAttribute('aria-live')).toBe('polite');
  });

  // ---- HAZARD segment (caution) + analysis card ---------------------------

  it('HAZARD: lights the caution segment (and the ALERT master) when sector hazard_level >= 5', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 5, radiation_level: 0.1 } };
    render();
    await flush();

    expect(seg('HAZARD').classList.contains('livec')).toBe(true);
    expect(bulb().classList.contains('on')).toBe(true);
  });

  it('HAZARD: does NOT light below the 5 threshold', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 4, radiation_level: 0.1 } };
    render();
    await flush();

    expect(litClassOf(seg('HAZARD'))).toBeUndefined();
    expect(bulb().classList.contains('on')).toBe(false);
  });

  it('HAZARD: click opens the self-contained analysis card with real sector data', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 7, radiation_level: 0.42 } };
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

  it('HAZARD card: opening moves focus to the close button (WCAG 2.4.3 focus-in)', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();

    act(() => {
      seg('HAZARD').click();
    });
    const closeBtn = container.querySelector('.annunciator-card-close') as HTMLButtonElement;
    expect(document.activeElement).toBe(closeBtn);
  });

  it('HAZARD card: Escape closes it and returns focus to the HAZARD lamp (WCAG 2.1.1 + 2.4.3 focus-restore)', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 7, radiation_level: 0 } };
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

  it('HAZARD card: role=dialog carries aria-modal="true"', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 7, radiation_level: 0 } };
    render();
    await flush();
    act(() => {
      seg('HAZARD').click();
    });
    expect(container.querySelector('[role="dialog"]')?.getAttribute('aria-modal')).toBe('true');
  });

  // ---- LAW segment (caution — a law-archetype contact is in-sector) -------

  it('LAW: lights iff a law-archetype contact is in-sector (NOT the player\'s own status)', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [LAW_CONTACT] } };
    render();
    await flush();

    expect(seg('LAW').classList.contains('livec')).toBe(true); // caution-tier, natural class -- no red special-case anymore
    expect(seg('LAW').classList.contains('live')).toBe(false);
    expect(bulb().classList.contains('on')).toBe(true);
    expect(seg('LAW').getAttribute('role')).toBe('status');
  });

  it('LAW: a raider or clear contact alone does NOT light LAW', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [RAIDER_CONTACT, CLEAR_CONTACT] } };
    render();
    await flush();

    expect(litClassOf(seg('LAW'))).toBeUndefined();
  });

  it('LAW: click requests the deck TACTICAL[THREAT] page', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [LAW_CONTACT] } };
    render();
    await flush();

    act(() => {
      seg('LAW').click();
    });
    expect(requestTacticalPageSpy).toHaveBeenCalledWith('threat');
  });

  // ---- THREAT segment (warn — a wanted/grey contact is in-sector) ---------

  it('THREAT: lights for a red-bucket (hostile NPC) contact in-sector', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [RAIDER_CONTACT] } };
    render();
    await flush();

    expect(seg('THREAT').classList.contains('live')).toBe(true);
    expect(bulb().classList.contains('on')).toBe(true);
  });

  it('THREAT: lights for a gray-bucket (suspicious player) contact in-sector', async () => {
    mockWsState = { sectorPlayers: [GREY_PLAYER_CONTACT] };
    render();
    await flush();

    expect(seg('THREAT').classList.contains('live')).toBe(true);
  });

  it('THREAT: a clear/blue contact alone does NOT light THREAT', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [CLEAR_CONTACT, LAW_CONTACT] } };
    render();
    await flush();

    expect(litClassOf(seg('THREAT'))).toBeUndefined();
  });

  it('THREAT: click requests the deck TACTICAL[TARGET] page', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [RAIDER_CONTACT] } };
    render();
    await flush();

    act(() => {
      seg('THREAT').click();
    });
    expect(requestTacticalPageSpy).toHaveBeenCalledWith('target');
  });

  it('THREAT: merges WS sectorPlayers and API players_present, excluding self', async () => {
    mockWsState = { sectorPlayers: [{ user_id: 'player-1', username: 'commander' }] }; // self via WS -- excluded
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [RAIDER_CONTACT] } };
    render();
    await flush();

    expect(seg('THREAT').classList.contains('live')).toBe(true);
  });

  // ---- COMM segment (info — persistent unread count) ----------------------

  it('COMM: lights (info-class, pulsing via CSS) iff unreadMessageCount > 0, and feeds the ALERT master', async () => {
    mockGameState = { ...mockGameState, unreadMessageCount: 3 };
    render();
    await flush();

    const commSeg = seg('COMM');
    expect(commSeg.classList.contains('livecm')).toBe(true);
    expect(commSeg.classList.contains('live')).toBe(false);
    expect(commSeg.classList.contains('livec')).toBe(false);
    expect(bulb().classList.contains('on')).toBe(true);
  });

  it('COMM: does not light at zero unread', async () => {
    await flush();
    expect(litClassOf(seg('COMM'))).toBeUndefined();
  });

  it('COMM: persistent — no auto-clear timer; clears only when unreadMessageCount drops back to zero', async () => {
    mockGameState = { ...mockGameState, unreadMessageCount: 1 };
    render();
    await flush();
    expect(seg('COMM').classList.contains('livecm')).toBe(true);

    // No dwell timer to advance -- the lamp must still be lit purely because
    // the count is still > 0.
    await act(async () => {
      vi.advanceTimersByTime(60000);
    });
    expect(seg('COMM').classList.contains('livecm')).toBe(true);

    // Only clears once the context-level count itself drops to zero
    // (elsewhere in the app -- CommsCrewPage's own markMessageRead flow).
    mockGameState = { ...mockGameState, unreadMessageCount: 0 };
    render();
    await flush();
    expect(litClassOf(seg('COMM'))).toBeUndefined();
  });

  it('COMM: click opens the comms panel at both possible screenIds (no ack call -- there is no single message id anymore)', async () => {
    mockGameState = { ...mockGameState, unreadMessageCount: 2 };
    render();
    await flush();

    // Should not throw reaching into useMFD/selectPage; click-through is a
    // pure navigation side effect, verified indirectly via no console error
    // and the segment staying lit (count is unchanged by the click itself).
    act(() => {
      seg('COMM').click();
    });
    expect(seg('COMM').classList.contains('livecm')).toBe(true);
  });

  // ---- ALERT master: lights iff ANY segment active, or siege/bounty -------

  it('ALERT master: lights iff any of HAZARD/LAW/THREAT/COMM is active', async () => {
    // Individually, each segment lights the SAME single master.
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 6 } };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(true);

    mockGameState = {
      ...mockGameState,
      currentSector: { ...mockGameState.currentSector!, hazard_level: 0, players_present: [LAW_CONTACT] },
    };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(true);

    mockGameState = {
      ...mockGameState,
      currentSector: { ...mockGameState.currentSector!, players_present: [RAIDER_CONTACT] },
    };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(true);

    mockGameState = {
      ...mockGameState,
      currentSector: { ...mockGameState.currentSector!, players_present: [] },
      unreadMessageCount: 5,
    };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(true);
  });

  it('ALERT master: lights for siegeActive ALONE — a master-only, segment-less trigger (restored by hub ruling)', async () => {
    mockGetOwnedPlanets.mockResolvedValue({ planets: [{ id: 'p1', underSiege: true }] });
    remountFresh();
    await flush();

    expect(bulb().classList.contains('on')).toBe(true);
    // Off-panel by design -- no segment lights for siege, matching the old
    // WARN-bulb-only shape (do not "fix" this asymmetry).
    expect(container.querySelectorAll('.live, .livec, .livecm')).toHaveLength(0);
  });

  it('ALERT master: lights for bountyActive ALONE — a master-only, segment-less trigger (restored by hub ruling)', async () => {
    mockGameState = { ...mockGameState, playerState: { id: 'player-1', username: 'commander', bounty_total: 5000 } };
    render();
    await flush();

    expect(bulb().classList.contains('on')).toBe(true);
    expect(container.querySelectorAll('.live, .livec, .livecm')).toHaveLength(0);
  });

  it('ALERT master: tap-acknowledge stops the flash but stays visible; auto-clears once the predicate resolves', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 6 } };
    render();
    await flush();

    expect(bulb().classList.contains('on')).toBe(true);
    act(() => {
      bulb().click();
    });
    expect(bulb().classList.contains('ack')).toBe(true);
    expect(bulb().classList.contains('on')).toBe(false);
    // Segment itself is untouched by the master ack -- still shows live.
    expect(seg('HAZARD').classList.contains('livec')).toBe(true);

    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 0 } };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(false);
    expect(bulb().classList.contains('ack')).toBe(false);
  });

  it('ALERT master: a fresh false->true edge re-flashes even after a prior ack', async () => {
    mockGameState = { ...mockGameState, unreadMessageCount: 1 };
    render();
    await flush();
    act(() => {
      bulb().click();
    });
    expect(bulb().classList.contains('ack')).toBe(true);

    mockGameState = { ...mockGameState, unreadMessageCount: 0 };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(false);
    expect(bulb().classList.contains('ack')).toBe(false);

    mockGameState = { ...mockGameState, unreadMessageCount: 1 };
    render();
    await flush();
    expect(bulb().classList.contains('on')).toBe(true);
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
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 7 } };
    render();
    await flush();

    expect(bulb().classList.contains('on')).toBe(false);
    expect(bulb().classList.contains('ack')).toBe(true);
  });

  // ---- a11y ----------------------------------------------------------------

  it('every button carries an aria-label naming the segment/bulb and its live state', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, hazard_level: 5 } };
    render();
    await flush();

    expect(seg('HAZARD').getAttribute('aria-label')).toContain('HAZARD');
    expect(seg('HAZARD').getAttribute('aria-label')).toContain('hazard level 5');
    expect(bulb().getAttribute('aria-label')).toContain('Master alert');
    expect(bulb().getAttribute('aria-label')).toContain('active');
    expect(seg('LAW').getAttribute('aria-label')).toContain('LAW');
    expect(seg('COMM').getAttribute('aria-label')).toContain('COMM');
  });
});
