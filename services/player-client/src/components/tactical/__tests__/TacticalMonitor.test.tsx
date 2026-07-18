// @vitest-environment jsdom
/**
 * TacticalMonitor — the cockpit TACTICAL deck-monitor host (WO-UI2-DECK-
 * RECONCILE, §05: TACTICAL [TARGET · THREAT]).
 *
 * Mirrors DeckPageTabs.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL). This file covers only the rail/host shell:
 * default page, tab switching, and prop passthrough to TacticalTargetPage.
 * TARGET's rep-color/ENGAGE/HAIL behavior and THREAT's law/mines/hazard
 * behavior are covered in their own page-level test files
 * (TacticalTargetPage.test.tsx, TacticalThreatPage.test.tsx).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// TacticalThreatPage fetches greyStatusAPI.getStatus() on mount -- resolved
// inert here since this file only proves the rail/host shell, not THREAT's
// own behavior (that's TacticalThreatPage.test.tsx's job).
vi.mock('../../../services/api', () => ({
  greyStatusAPI: {
    getStatus: () => Promise.resolve({ isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null }),
  },
  combatAPI: {
    engage: vi.fn(),
    getStatus: vi.fn(),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: 'player-1', mines: 0, is_docked: false, is_landed: false },
    currentSector: { hazard_level: 0, radiation_level: 0, type: 'STANDARD' },
    refreshPlayerState: vi.fn(),
    sendPlayerMessage: vi.fn(),
    deployMines: vi.fn(),
    updatePlayerCredits: vi.fn(),
  }),
}));

// TacticalTargetPage now calls useWindshieldFlight() directly (WO-TACTICAL-
// APPROACH-ENGAGE-SCROLL Part B) -- a real WindshieldFlightProvider wraps
// every mount below (mirrors GameDashboard.tsx's own real wrapping), which
// pulls in AutopilotContext transitively (WindshieldFlightProvider calls
// useAutopilot()) -- mocked the same way WindshieldTableau.test.tsx/
// TacticalTargetPage.test.tsx already do.
vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: 'idle', abort: vi.fn() }),
}));

import TacticalMonitor, { type TacticalContact } from '../TacticalMonitor';
import { requestTacticalPage, __resetDeckNavBusForTests } from '../../../services/deckNavBus';
import { WindshieldFlightProvider } from '../../../contexts/WindshieldFlightContext';

describe('TacticalMonitor', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    // deckNavBus is a real module-level singleton (no mock) -- reset its
    // latched request between tests so an earlier test's requestTactical
    // Page() call can't leak into a later one via `getLatestTacticalPage
    // Request()`'s pending-request pickup.
    __resetDeckNavBusForTests();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const CONTACTS: TacticalContact[] = [
    { player_id: 'p1', ship_id: 's1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
  ];

  const mount = async (props: Partial<React.ComponentProps<typeof TacticalMonitor>> = {}) => {
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <TacticalMonitor
            contacts={props.contacts ?? CONTACTS}
            selectedShipId={props.selectedShipId}
            onSelectContact={props.onSelectContact}
          />
        </WindshieldFlightProvider>
      );
    });
    await flush();
  };

  it('renders TARGET as the default page, with the header + a 2-tab rail', async () => {
    await mount();

    const header = container.querySelector('.mhead .mtitle')!;
    expect(header.textContent).toBe('TACTICAL');

    const tabs = Array.from(container.querySelectorAll('.deck-tab-rail .deck-tab-btn')).map((b) => b.textContent);
    expect(tabs).toEqual(['TARGET', 'THREAT']);

    // TARGET content is live by default (the seeded contact renders).
    expect(container.querySelector('.target-contact-list')).toBeTruthy();
    expect(container.textContent).toContain('Vega');
    expect(container.querySelector('.threat-section')).toBeNull();
  });

  // ---- WO-UI0-SHELL-TRANSPLANT (Leaf L3): re-emitted monitor anatomy -----

  it('anatomy: .mon > .mhead(.mtitle+.hsub) + .mbody + bottom .skrow, softkeys relocated out of the header, roving tabindex intact', async () => {
    await mount();

    const mon = container.querySelector('.mon.tactical-monitor')!;
    expect(mon).toBeTruthy();

    // Exactly 3 direct children, in DOM order: header, body, then the
    // softkey row LAST (artifact monTac(): `.mon` > `.mhead` + `.mbody` +
    // bottom `.skrow`).
    const children = Array.from(mon.children).map((el) => el.className);
    expect(children).toEqual(['mhead', 'mbody', 'skrow']);

    const mtitle = mon.querySelector('.mhead .mtitle')!;
    expect(mtitle.textContent).toBe('TACTICAL');
    const hsub = mon.querySelector('.mhead .hsub')!;
    expect(hsub.textContent).toBe('1 CONTACT'); // the seeded single contact

    // The tablist lives in the bottom .skrow now, NOT the header.
    expect(mon.querySelector('.mhead [role="tablist"]')).toBeNull();
    const skrow = mon.querySelector('.skrow')!;
    const rail = skrow.querySelector('[role="tablist"].deck-tab-rail')!;
    expect(rail).toBeTruthy();

    // Roving tabindex (SoftkeyRail.tsx, untouched) still wired through the
    // relocated rail: exactly the active tab is a tab stop.
    const tabs = Array.from(rail.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false']);
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1]);
  });

  it('switches to THREAT on tab click, unmounting TARGET content', async () => {
    await mount();

    const threatTab = Array.from(container.querySelectorAll('.deck-tab-btn')).find((b) => b.textContent === 'THREAT')!;
    await click(threatTab);
    await flush();

    expect(container.querySelector('.target-contact-list')).toBeNull();
    expect(container.querySelector('.threat-section')).toBeTruthy();
  });

  it('passes contacts/selectedShipId/onSelectContact through to TacticalTargetPage unchanged', async () => {
    const onSelectContact = vi.fn();
    await mount({
      contacts: [{ player_id: 'p2', ship_id: 's2', username: 'Halcyon', reputation_tier: 'Suspicious', personal_reputation: -80 }],
      selectedShipId: 's2',
      onSelectContact,
    });

    expect(container.textContent).toContain('Halcyon');
    const name = container.querySelector('.target-contact-name')!;
    await click(name);
    expect(onSelectContact).toHaveBeenCalledTimes(1);
  });

  it('sets tabpanel id/aria-labelledby to match the active page', async () => {
    await mount();
    let panel = container.querySelector('.mbody[role="tabpanel"]')!;
    expect(panel.id).toBe('tactical-panel-target');
    expect(panel.getAttribute('aria-labelledby')).toBe('tactical-tab-target');

    const threatTab = Array.from(container.querySelectorAll('.deck-tab-btn')).find((b) => b.textContent === 'THREAT')!;
    await click(threatTab);
    await flush();

    panel = container.querySelector('.mbody[role="tabpanel"]')!;
    expect(panel.id).toBe('tactical-panel-threat');
    expect(panel.getAttribute('aria-labelledby')).toBe('tactical-tab-threat');
  });

  // ---- WO-UI1-CHROME-COMPLETE: annunciator LAW/THREAT click-through -------

  it('deckNavBus: a requestTacticalPage("threat") call flips the softkey to THREAT while mounted', async () => {
    await mount();
    expect(container.querySelector('.threat-section')).toBeNull();

    await act(async () => {
      requestTacticalPage('threat');
    });
    await flush();

    expect(container.querySelector('.threat-section')).toBeTruthy();
    expect(container.querySelector('.target-contact-list')).toBeNull();
  });

  it('deckNavBus: a request that latched BEFORE mount is picked up on mount (docked/landed click, then fly)', async () => {
    requestTacticalPage('threat');
    await mount();

    expect(container.querySelector('.threat-section')).toBeTruthy();
  });

  it('deckNavBus: requestTacticalPage("target") flips back to TARGET', async () => {
    requestTacticalPage('threat');
    await mount();
    expect(container.querySelector('.threat-section')).toBeTruthy();

    await act(async () => {
      requestTacticalPage('target');
    });
    await flush();

    expect(container.querySelector('.target-contact-list')).toBeTruthy();
    expect(container.querySelector('.threat-section')).toBeNull();
  });
});
