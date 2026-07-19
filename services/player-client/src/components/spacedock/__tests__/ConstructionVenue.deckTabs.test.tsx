// @vitest-environment jsdom
/**
 * ConstructionVenue — DeckPageTabs migration (WO-UI2-CANON-A-CONSTRUCTION).
 *
 * Proves the hand-rolled `.construction-tabs` tablist now mounts the
 * shared cockpit/DeckPageTabs.tsx rail: the rendered root carries BOTH
 * `deck-tab-rail` (base rail behavior) and `construction-tabs` (this
 * venue's skin, re-scoped in construction-venue.css), the tablist/tab
 * roles + tabpanel aria wiring survive the swap, and roving-tabindex
 * keyboard nav (gained from DeckPageTabs, absent in the old hand-rolled
 * markup) works. Same jsdom + react-dom/client createRoot + act() harness
 * as the sibling ConstructionVenue.catalog.test.tsx in this directory.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../services/api', () => ({
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: { cargo: { contents: { ore: 500, equipment: 500, organics: 500 } } },
    refreshPlayerState: vi.fn(),
    loadShips: vi.fn(),
  }),
}));

import ConstructionVenue from '../ConstructionVenue';

const QUOTE = {
  ship_type: 'scout',
  total_cost: 10000,
  deposit: 2000,
  build_days: 3,
  resources_required: { ore: 100, equipment: 50, organics: 20 },
  requires_tier_a: false,
  uses_specialized_slip: false,
};

const RESERVATION = {
  id: 'res-1',
  state: 'frame_assembly',
  ship_type: 'scout',
  resources_required: { ore: 100, equipment: 50, organics: 20 },
  resources_delivered: {},
};

function mockFetch() {
  return vi.fn((url: string) => {
    if (url.includes('/construction/quotes')) {
      return Promise.resolve({ ok: true, json: async () => ({ quotes: [QUOTE] }) });
    }
    if (url.includes('/construction/reservations/mine')) {
      return Promise.resolve({ ok: true, json: async () => ({ reservations: [RESERVATION] }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

const VENUE_PROPS = {
  stationId: 'station-1',
  stationName: 'Test Dock',
  tier: 'A' as const,
  credits: 100000,
  onCreditsDelta: vi.fn(),
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

describe('ConstructionVenue — DeckPageTabs migration', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    vi.stubGlobal('fetch', mockFetch());
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('renders the tablist via DeckPageTabs, co-classed with the venue skin, with tabpanel wired to the active id', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const rail = container.querySelector('[role="tablist"]');
    expect(rail).toBeTruthy();
    expect(rail!.className).toContain('deck-tab-rail');
    expect(rail!.className).toContain('construction-tabs');
    expect(rail!.getAttribute('aria-label')).toBe('Construction view');

    const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
    expect(tabs).toHaveLength(2);
    expect(tabs[0].textContent).toContain('Ship Order Book');
    expect(tabs[0].id).toBe('construction-tab-orders');
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(tabs[0].className).toContain('deck-tab-btn');
    expect(tabs[0].className).toContain('active');
    expect(tabs[1].textContent).toContain('My Builds');
    expect(tabs[1].id).toBe('construction-tab-builds');
    expect(tabs[1].getAttribute('aria-selected')).toBe('false');

    const panel = container.querySelector('[role="tabpanel"]');
    expect(panel).toBeTruthy();
    expect(panel!.id).toBe('construction-panel-orders');
    expect(panel!.getAttribute('aria-labelledby')).toBe('construction-tab-orders');
    expect(panel!.querySelector('.construction-orders')).toBeTruthy();
  });

  it('switches the active tab + tabpanel on click, preserving prior tab-switch behavior', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const buildsTab = container.querySelector('#construction-tab-builds') as HTMLButtonElement;
    expect(buildsTab).toBeTruthy();
    await act(async () => { buildsTab.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(buildsTab.getAttribute('aria-selected')).toBe('true');
    expect(buildsTab.className).toContain('active');

    const panel = container.querySelector('[role="tabpanel"]');
    expect(panel!.id).toBe('construction-panel-builds');
    expect(panel!.getAttribute('aria-labelledby')).toBe('construction-tab-builds');
    expect(panel!.querySelector('.construction-builds')).toBeTruthy();
  });

  it('gains roving-tabindex ArrowRight keyboard nav from DeckPageTabs (absent in the old hand-rolled tablist)', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const ordersTab = container.querySelector('#construction-tab-orders') as HTMLButtonElement;
    const buildsTab = container.querySelector('#construction-tab-builds') as HTMLButtonElement;
    expect(ordersTab.tabIndex).toBe(0);
    expect(buildsTab.tabIndex).toBe(-1);

    await act(async () => {
      ordersTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });

    expect(buildsTab.getAttribute('aria-selected')).toBe('true');
  });
});
