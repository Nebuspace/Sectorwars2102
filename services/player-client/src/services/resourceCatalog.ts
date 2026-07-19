/**
 * Resource Registry catalog client (WO-ARCH-RES-3-FE-CATALOG).
 *
 * Fetches the seeded resource catalog (GET /api/v1/resources) ONCE per
 * session and caches it in memory — every consumer below shares the same
 * fetch/cache via getResourceCatalog() (concurrent callers share one
 * in-flight request). A resource newly inserted into the registry surfaces
 * automatically wherever code reads its LABEL through this module; no
 * frontend code change required to pick it up.
 *
 * The catalog's `icon` field is currently a documented placeholder — it
 * defaults to the resource's own `name` slug (see
 * resource_registry_seeder.py's module docstring: "no glyph/asset key has
 * been designed yet"), not a real glyph, so it is deliberately NOT used for
 * display here. Icon/colour instead come from the DEFAULT_ICONS/DEFAULT_
 * COLORS tables below — the hand-picked glyphs each consumer used to
 * hardcode individually, now defined once — with a generic fallback for any
 * resource (including a brand-new registry row) that isn't in those tables.
 * When the backend grows a real icon/colour field, repoint resourceIcon()/
 * resourceColor() at it without touching call sites.
 */
import { resourceAPI } from './api';

export interface ResourceCatalogEntry {
  name: string;
  label: string | null;
  icon: string | null;
  category: string | null;
  base_price: number | null;
  price_range_min: number | null;
  price_range_max: number | null;
  is_storable: boolean;
  is_producible: boolean;
}

// Hand-picked glyphs for every canon resource with an established identity
// in the cockpit today, plus the citadel/planet domain's legacy `fuel_ore`
// vocabulary (gameserver commodity_economy.COMMODITY_ALIASES: the citadel
// API's/planet Column's name for `ore`, kept distinct from `ore` here since
// the citadel safe and the production stockpile are different UI contexts).
const DEFAULT_ICONS: Record<string, string> = {
  fuel: '⛽',
  fuel_ore: '⛽',
  organics: '🌿',
  gourmet_food: '🍽️',
  equipment: '⚙️',
  exotic_technology: '🔬',
  luxury_goods: '💎',
  ore: '⛏️',
  colonists: '👥',
  combat_drones: '🛰️',
  quantum_shards: '💠',
  quantum_crystals: '🔷',
  prismatic_ore: '🪨',
  lumen_crystals: '✨',
  // 9th market commodity (models/station.py DEFAULT_COMMODITIES, bang_import
  // _COMMODITY_DEFAULTS, trading_service — a real MarketPrice.commodity value,
  // absent only from the registry seed per admin resourceCatalog.ts's own
  // documented gap note). Was silently falling to the generic 📦 (WO-ARCH-
  // RES-3B B4 key-domain audit).
  precious_metals: '🪙',
};

const DEFAULT_COLORS: Record<string, string> = {
  fuel: '#ff6b6b',
  fuel_ore: '#ff6b6b',
  organics: '#51cf66',
  gourmet_food: '#f783ac',
  equipment: '#339af0',
  exotic_technology: '#22b8cf',
  luxury_goods: '#e599f7',
  ore: '#a99274',
  colonists: '#f59f00',
  combat_drones: '#ff8787',
  quantum_shards: '#63e6be',
  quantum_crystals: '#66d9e8',
  prismatic_ore: '#da77f2',
  lumen_crystals: '#ffd43b',
  precious_metals: '#d4af37',
};

// Label text for keys the catalog itself can never resolve (mismatched
// domain vocabulary — `fuel_ore` isn't a registry `name`, see above).
const DEFAULT_LABELS: Record<string, string> = {
  fuel_ore: 'Fuel Ore',
};

const GENERIC_ICON = '📦';
const GENERIC_COLOR = '#adb5bd';

/** "quantum_shards" -> "Quantum Shards" — last-resort label for an unknown key. */
const prettify = (key: string): string =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

let cachedCatalog: ResourceCatalogEntry[] | null = null;
let inFlight: Promise<ResourceCatalogEntry[]> | null = null;
const listeners = new Set<() => void>();

/**
 * Fetch (or return the cached) resource catalog. Concurrent callers share
 * one in-flight request. A failed fetch is NOT cached, so the next caller
 * retries rather than being stuck on a transient error forever.
 */
export function getResourceCatalog(): Promise<ResourceCatalogEntry[]> {
  if (cachedCatalog) return Promise.resolve(cachedCatalog);
  if (!inFlight) {
    inFlight = resourceAPI
      .list()
      .then((data: ResourceCatalogEntry[]) => {
        cachedCatalog = Array.isArray(data) ? data : [];
        inFlight = null;
        listeners.forEach((fn) => fn());
        return cachedCatalog;
      })
      .catch((err: unknown) => {
        inFlight = null;
        throw err;
      });
  }
  return inFlight;
}

/** Synchronous snapshot of the cached catalog — null until the first fetch resolves. */
export function getCachedResourceCatalog(): ResourceCatalogEntry[] | null {
  return cachedCatalog;
}

/** Subscribe to catalog arrival; returns an unsubscribe function. Used by useResourceCatalog. */
export function subscribeResourceCatalog(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

const findEntry = (
  catalog: ResourceCatalogEntry[] | null,
  name: string
): ResourceCatalogEntry | undefined => catalog?.find((r) => r.name === name);

/**
 * Display label for a resource key: registry label (when the catalog has
 * loaded and knows this name) -> site default -> prettified key. Safe to
 * call before the catalog has loaded (degrades gracefully to the fallback
 * chain, then upgrades once the fetch lands and callers re-render).
 */
export function resourceLabel(
  name: string,
  catalog: ResourceCatalogEntry[] | null = cachedCatalog
): string {
  return findEntry(catalog, name)?.label || DEFAULT_LABELS[name] || prettify(name);
}

/** Display glyph for a resource key — see module docstring on why this never reads the catalog's own `icon`. */
export function resourceIcon(name: string): string {
  return DEFAULT_ICONS[name] || GENERIC_ICON;
}

/** Accent colour for a resource key — the catalog carries no colour field. */
export function resourceColor(name: string): string {
  return DEFAULT_COLORS[name] || GENERIC_COLOR;
}
