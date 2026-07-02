/**
 * resourceCatalog — focused unit tests (WO-ARCH-RES-3-FE-CATALOG).
 *
 * Asserts the two contracts the frontend catalog swap depends on:
 *   (a) label/icon/colour fall back gracefully for a resource the catalog
 *       hasn't loaded yet, doesn't know about, or that's a totally new
 *       registry row (no crash, no missing label) — the ACCEPT criterion.
 *   (b) getResourceCatalog() fetches ONCE and shares the result: concurrent
 *       callers share one in-flight request, and a failed fetch is not
 *       cached (the next call retries). The module keeps its cache as
 *       private state, so each case in (b) resets the module registry and
 *       re-imports fresh rather than relying on `it` execution order.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  resourceAPI: { list: vi.fn() },
}));

import {
  resourceColor,
  resourceIcon,
  resourceLabel,
  type ResourceCatalogEntry,
} from '../resourceCatalog';

const FUEL: ResourceCatalogEntry = {
  name: 'fuel',
  label: 'Fuel',
  icon: 'fuel',
  category: 'core_commodity',
  base_price: 20,
  price_range_min: 15,
  price_range_max: 25,
  is_storable: false,
  is_producible: true,
};

describe('resourceLabel / resourceIcon / resourceColor — fallback chain', () => {
  it('prefers the registry label when the catalog knows the name', () => {
    expect(resourceLabel('fuel', [FUEL])).toBe('Fuel');
  });

  it('falls back to the site default for a domain-vocabulary key the registry never resolves (citadel fuel_ore -> ore)', () => {
    // Registry name is `fuel`, never `fuel_ore` — confirms the citadel/safe
    // UI's legacy key keeps its own label rather than silently mismatching.
    expect(resourceLabel('fuel_ore', [FUEL])).toBe('Fuel Ore');
  });

  it('prettifies a brand-new registry row with no local default — no crash, no missing label', () => {
    const NEW_ROW: ResourceCatalogEntry = {
      ...FUEL,
      name: 'nebula_ash',
      label: null, // registry row exists but the seeder hasn't filled a label yet
    };
    expect(resourceLabel('nebula_ash', [NEW_ROW])).toBe('Nebula Ash');
  });

  it('prettifies an entirely unknown key when the catalog has not loaded (null)', () => {
    expect(resourceLabel('quantum_shards', null)).toBe('Quantum Shards');
  });

  it('resourceIcon/resourceColor degrade to the generic default for an unmapped key', () => {
    expect(resourceIcon('nebula_ash')).toBe('📦');
    expect(resourceColor('nebula_ash')).toBe('#adb5bd');
  });

  it('resourceIcon returns the hand-picked default for a known key', () => {
    expect(resourceIcon('organics')).toBe('🌿');
  });

  it('resourceIcon/resourceColor return the exact retired trio-surface hardcodes for fuel/organics/equipment/colonists', () => {
    // Visual no-change assertion (WO-ARCH-RES-3A accept 4) — these must match
    // the ProductionDashboard hardcodes byte-for-byte.
    expect(resourceIcon('fuel')).toBe('⛽');
    expect(resourceIcon('organics')).toBe('🌿');
    expect(resourceIcon('equipment')).toBe('⚙️');
    expect(resourceIcon('colonists')).toBe('👥');
    expect(resourceColor('fuel')).toBe('#ff6b6b');
    expect(resourceColor('organics')).toBe('#51cf66');
    expect(resourceColor('equipment')).toBe('#339af0');
    expect(resourceColor('colonists')).toBe('#f59f00');
  });

  it('resourceIcon/resourceColor cover precious_metals (WO-ARCH-RES-3B B4 key-domain audit: the 9th market commodity — models/station.py DEFAULT_COMMODITIES, bang_import _COMMODITY_DEFAULTS, trading_service — was silently falling to the generic 📦)', () => {
    expect(resourceIcon('precious_metals')).toBe('🪙');
    expect(resourceColor('precious_metals')).toBe('#d4af37');
  });

  it('EXTENSIBILITY: a brand-new registry row resolves through resourceLabel with zero code change', () => {
    // WO-ARCH-RES-3A accept 2 — the epic's whole point. A row the module has
    // never seen (no DEFAULT_LABELS entry, no hand-picked icon/colour) still
    // resolves correctly through the catalog alone.
    const catalog: ResourceCatalogEntry[] = [
      { name: 'unobtainium', label: 'Unobtainium', icon: 'unobtainium', category: 'rare', base_price: 999, price_range_min: 900, price_range_max: 1100, is_storable: true, is_producible: false },
    ];
    expect(resourceLabel('unobtainium', catalog)).toBe('Unobtainium');
  });
});

describe('getResourceCatalog — fetch-once cache', () => {
  // The cache is module-private state, so each test gets a clean module
  // registry (a fresh cachedCatalog/inFlight) rather than sharing state
  // left over from a previous test's fetch.
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shares one in-flight request across concurrent callers', async () => {
    const { resourceAPI } = await import('../api');
    const { getResourceCatalog } = await import('../resourceCatalog');
    const listMock = resourceAPI.list as unknown as ReturnType<typeof vi.fn>;

    let resolveFetch: (v: ResourceCatalogEntry[]) => void;
    listMock.mockReturnValueOnce(
      new Promise<ResourceCatalogEntry[]>((resolve) => {
        resolveFetch = resolve;
      })
    );

    const p1 = getResourceCatalog();
    const p2 = getResourceCatalog();
    resolveFetch!([FUEL]);

    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toBe(r2);
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it('does not cache a rejected fetch — the next call retries', async () => {
    const { resourceAPI } = await import('../api');
    const { getResourceCatalog } = await import('../resourceCatalog');
    const listMock = resourceAPI.list as unknown as ReturnType<typeof vi.fn>;

    listMock.mockRejectedValueOnce(new Error('network error'));
    await expect(getResourceCatalog()).rejects.toThrow('network error');

    listMock.mockResolvedValueOnce([FUEL]);
    const result = await getResourceCatalog();
    expect(result).toEqual([FUEL]);
    expect(listMock).toHaveBeenCalledTimes(2);
  });
});
