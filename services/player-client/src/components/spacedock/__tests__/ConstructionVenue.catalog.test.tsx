// @vitest-environment jsdom
/**
 * ConstructionVenue — resource catalog wiring (WO-ARCH-RES-3A-FE-CATALOG-RATIFY, accept 7).
 *
 * Confirms the venue's icon/label sourcing survived the shared-catalog swap
 * byte-identical: equipment keeps its construction-context 🔩 override (not
 * the catalog default ⚙️), ore/organics use the shared defaults, and the
 * delivery inputs' aria-labels resolve through getLabel(). The resource
 * catalog fetch is left permanently pending below — resourceIcon() never
 * reads the catalog at all (local defaults only, see resourceCatalog.ts),
 * and getLabel() for ore/equipment/organics prettifies to the same text the
 * registry itself carries, so this exercises the steady-state, catalog-
 * absent rendering path this UI actually runs under today.
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

describe('ConstructionVenue — catalog icon/label wiring', () => {
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

  it('renders the equipment 🔩 override + shared ore ⛏️ / organics 🌿 defaults on an order-book quote card', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    // Flush the quotes/reservations fetch resolution + resulting state update.
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const text = container.textContent || '';
    expect(text).toContain('🔩'); // construction-context equipment override
    expect(text).toContain('⛏️'); // ore, shared default
    expect(text).toContain('🌿'); // organics, shared default
    expect(text).not.toContain('⚙️'); // catalog's plain equipment glyph must not leak into this context
  });

  it('delivery inputs carry getLabel-sourced aria-labels once the deliver panel is opened', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const buildsTab = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('My Builds'));
    expect(buildsTab).toBeTruthy();
    await act(async () => { buildsTab!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    const deliverBtn = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('Deliver'));
    expect(deliverBtn).toBeTruthy();
    await act(async () => { deliverBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(container.querySelector('input[aria-label="Ore to deliver"]')).toBeTruthy();
    expect(container.querySelector('input[aria-label="Equipment to deliver"]')).toBeTruthy();
    expect(container.querySelector('input[aria-label="Organics to deliver"]')).toBeTruthy();
  });
});
