// @vitest-environment jsdom
/**
 * CockpitColonyManagement — DeckPageTabs migration (WO-UI2-CANON-A-COLONY).
 *
 * Proves the hand-rolled `.cmc-tabbar`/`.cmc-tab` 7-tab tablist now mounts
 * the shared cockpit/DeckPageTabs.tsx rail: the rendered root carries BOTH
 * `deck-tab-rail` (base rail behavior) and `cmc-tabbar` (this venue's own
 * skin, re-scoped in cockpit-colony.css), each of the 7 tabs still lights
 * up in its own Law-5 accent (now via DeckPageTabs' per-page `accent`
 * rather than a hand-rolled inline style), the tablist/tab→tabpanel aria
 * contract (`cmc-tab-{id}` / `cmc-panel-{id}`) is wired, and the pre-
 * existing `setShowSpecialization(false)` tab-switch side-effect survived
 * the fold into DeckPageTabs' `onSelect`.
 *
 * The 5 tab-body managers (CitadelPanel/GridPanel/TerraformPanel/
 * ResearchPanel/ProductionPanel) each wrap a heavyweight existing manager
 * (CitadelManager/GridManager/TerraformingPanel/EmpireResearchPanel) with
 * its own deep GameContext/API surface unrelated to this migration, so —
 * mirroring the proven GameLayout.modeStationPersistentRail.test.tsx
 * pattern of stubbing irrelevant child chrome via `vi.mock(..., () => ({
 * default: () => <div data-testid=".../> }))` — they're stubbed here to
 * isolate the tab-rail migration itself.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// vi.hoisted: vi.mock factories are hoisted above ALL other top-level
// statements (including a plain `const`), so a value the factory closes
// over must itself be created inside vi.hoisted() to avoid a TDZ
// ReferenceError at mock-resolution time.
const { getOwnedPlanets } = vi.hoisted(() => ({
  getOwnedPlanets: vi.fn(() =>
    Promise.resolve({ planets: [{ id: 'planet-1', buildings: [] }] }),
  ),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: { planetary: { getOwnedPlanets } },
}));

vi.mock('../CitadelPanel', () => ({ default: () => <div data-testid="panel-citadel" /> }));
vi.mock('../GridPanel', () => ({ default: () => <div data-testid="panel-grid" /> }));
vi.mock('../TerraformPanel', () => ({ default: () => <div data-testid="panel-terraform" /> }));
vi.mock('../ResearchPanel', () => ({ default: () => <div data-testid="panel-research" /> }));
vi.mock('../ProductionPanel', () => ({
  default: ({ onOpenSpecialization }: { onOpenSpecialization: () => void }) => (
    <div data-testid="panel-production">
      <button type="button" data-testid="open-specialization" onClick={onOpenSpecialization}>
        open specialization
      </button>
    </div>
  ),
}));
vi.mock('../../planetary/SpecializationDrawer', () => ({
  default: () => <div data-testid="specialization-drawer" />,
}));

import CockpitColonyManagement from '../CockpitColonyManagement';

const BASE_PROPS = {
  planetId: 'planet-1',
  playerCredits: 10_000,
  citadelInfo: {},
  landedPlanetDetail: {},
  productionLines: [],
  overflowResources: [],
  allocations: { fuel: 0, organics: 0, equipment: 0 },
  productionRates: null,
  allocBudget: 10,
  totalColonists: 10,
  onSetAllocations: vi.fn(),
  onStoreToSafe: vi.fn(),
  onOpsChange: vi.fn(),
};

// key -> the Law-5 accent CockpitColonyManagement.tsx defines for that tab.
const TAB_ACCENTS: Record<string, string> = {
  citadel: '#fbbf24',
  grid: '#a78bfa',
  terraform: '#34d399',
  research: '#22d3ee',
  production: '#7dd3fc',
  defense: '#f87171',
  safe: '#2dd4bf',
};

describe('CockpitColonyManagement — DeckPageTabs migration (WO-UI2-CANON-A-COLONY)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<CockpitColonyManagement {...BASE_PROPS} />);
    });
    await flush();
  };

  beforeEach(() => {
    getOwnedPlanets.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders all 7 tabs via DeckPageTabs, co-classed with the venue skin, each carrying its own --tab-accent', async () => {
    await mount();

    const tablist = container.querySelector('[role="tablist"]');
    expect(tablist).toBeTruthy();
    expect(tablist!.classList.contains('deck-tab-rail')).toBe(true);
    expect(tablist!.classList.contains('cmc-tabbar')).toBe(true);
    expect(tablist!.getAttribute('aria-label')).toBe('Colony management');

    const tabs = Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    expect(tabs.length).toBe(7);

    Object.entries(TAB_ACCENTS).forEach(([key, accent]) => {
      const tab = tabs.find((t) => t.id === `cmc-tab-${key}`);
      expect(tab, `missing rendered tab for "${key}"`).toBeTruthy();
      expect(tab!.classList.contains('deck-tab-btn')).toBe(true);
      expect(tab!.style.getPropertyValue('--tab-accent')).toBe(accent);
      expect(tab!.getAttribute('aria-controls')).toBe(`cmc-panel-${key}`);
    });

    // Citadel is the default landing tab.
    const citadelTab = tabs.find((t) => t.id === 'cmc-tab-citadel')!;
    expect(citadelTab.getAttribute('aria-selected')).toBe('true');
    expect(citadelTab.classList.contains('active')).toBe(true);
  });

  it('wires the tablist->tabpanel aria contract (cmc-tab-{id} / cmc-panel-{id}) and switches the rendered panel', async () => {
    await mount();

    let panel = container.querySelector('[role="tabpanel"]') as HTMLElement;
    expect(panel.id).toBe('cmc-panel-citadel');
    expect(panel.getAttribute('aria-labelledby')).toBe('cmc-tab-citadel');
    expect(container.querySelector('[data-testid="panel-citadel"]')).toBeTruthy();

    const gridTab = container.querySelector('#cmc-tab-grid') as HTMLButtonElement;
    await act(async () => {
      gridTab.click();
    });
    await flush();

    panel = container.querySelector('[role="tabpanel"]') as HTMLElement;
    expect(panel.id).toBe('cmc-panel-grid');
    expect(panel.getAttribute('aria-labelledby')).toBe('cmc-tab-grid');
    expect(container.querySelector('[data-testid="panel-grid"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="panel-citadel"]')).toBeFalsy();
    expect(gridTab.getAttribute('aria-selected')).toBe('true');
  });

  it('folding setShowSpecialization(false) into onSelect still fires on tab switch (drawer dismisses)', async () => {
    await mount();

    // Switch to Production and open the in-tab Specialization drawer.
    const productionTab = container.querySelector('#cmc-tab-production') as HTMLButtonElement;
    await act(async () => {
      productionTab.click();
    });
    await flush();
    expect(container.querySelector('[data-testid="panel-production"]')).toBeTruthy();

    const openBtn = container.querySelector('[data-testid="open-specialization"]') as HTMLButtonElement;
    await act(async () => {
      openBtn.click();
    });
    await flush();
    expect(container.querySelector('[data-testid="specialization-drawer"]')).toBeTruthy();

    // Switching tabs must dismiss the drawer — the folded-in side-effect
    // (previously the button's own onClick, now DeckPageTabs' onSelect).
    const safeTab = container.querySelector('#cmc-tab-safe') as HTMLButtonElement;
    await act(async () => {
      safeTab.click();
    });
    await flush();

    expect(container.querySelector('[data-testid="specialization-drawer"]')).toBeFalsy();
    const panel = container.querySelector('[role="tabpanel"]') as HTMLElement;
    expect(panel.id).toBe('cmc-panel-safe');
    expect(panel.getAttribute('aria-labelledby')).toBe('cmc-tab-safe');
  });
});
