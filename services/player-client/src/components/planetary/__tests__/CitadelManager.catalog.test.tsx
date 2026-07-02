// @vitest-environment jsdom
/**
 * CitadelManager — resource catalog wiring (WO-ARCH-RES-3A-FE-CATALOG-RATIFY, accept 5).
 *
 * The upgrade panel's resource_cost line uses the citadel/planet domain's
 * `fuel_ore` key — a name the resource registry never resolves (its row is
 * named `fuel`, see resourceCatalog.ts's DEFAULT_LABELS comment). This
 * asserts the rendered line is '⛽ Fuel Ore 1,500' BOTH before the catalog
 * fetch resolves and after it lands with a catalog that (correctly) has no
 * `fuel_ore` row — no label flash to the registry's `fuel`/'Ore' text.
 *
 * services/resourceCatalog.ts keeps its fetch cache as module-private state,
 * so each case below resets the module registry and re-imports fresh rather
 * than sharing state left over from a previous case's fetch (same pattern as
 * services/__tests__/resourceCatalog.test.ts).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const CITADEL_INFO = {
  success: true,
  planet_id: 'planet-1',
  planet_name: 'Test World',
  citadel_level: 1,
  citadel_name: 'Outpost',
  max_population: 1000,
  safe_storage: 100000,
  safe_credits: 0,
  drone_capacity: 10,
  is_upgrading: false,
  next_level: {
    level: 2,
    name: 'Settlement',
    upgrade_cost: 5000,
    upgrade_hours: 4,
    resource_cost: { fuel_ore: 1500 },
    max_population: 5000,
    safe_storage: 500000,
    drone_capacity: 25,
  },
};

describe('CitadelManager — upgrade resource_cost line', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot> | null;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(async () => {
    if (root) await act(async () => { root!.unmount(); });
    container?.remove();
    root = null;
    vi.clearAllMocks();
  });

  it("renders '⛽ Fuel Ore 1,500' before the catalog fetch resolves", async () => {
    vi.doMock('../../../services/api', () => ({
      citadelAPI: { getInfo: vi.fn(() => Promise.resolve(CITADEL_INFO)) },
      resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
    }));
    const { default: CitadelManager } = await import('../CitadelManager');

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root!.render(<CitadelManager planetId="planet-1" playerCredits={10000} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain('⛽ Fuel Ore 1,500');
  });

  it("renders the identical '⛽ Fuel Ore 1,500' line once the catalog resolves (registry has no fuel_ore row)", async () => {
    vi.doMock('../../../services/api', () => ({
      citadelAPI: { getInfo: vi.fn(() => Promise.resolve(CITADEL_INFO)) },
      resourceAPI: {
        list: vi.fn(() => Promise.resolve([
          { name: 'fuel', label: 'Fuel', icon: 'fuel', category: 'core_commodity', base_price: 20, price_range_min: 15, price_range_max: 25, is_storable: false, is_producible: true },
          { name: 'ore', label: 'Ore', icon: 'ore', category: 'core_commodity', base_price: 30, price_range_min: 15, price_range_max: 45, is_storable: true, is_producible: true },
        ])),
      },
    }));
    const { default: CitadelManager } = await import('../CitadelManager');

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root!.render(<CitadelManager planetId="planet-1" playerCredits={10000} />);
    });
    // Flush the citadel-info fetch, then the catalog fetch's listener re-render.
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain('⛽ Fuel Ore 1,500');
  });
});
