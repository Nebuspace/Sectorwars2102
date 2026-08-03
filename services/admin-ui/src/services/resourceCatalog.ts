/**
 * Resource Registry catalog client (WO-ARCH-RES-3-FE-CATALOG).
 *
 * Fetches the seeded resource catalog (GET /api/v1/resources) ONCE per
 * session and caches it in memory — every consumer shares the same fetch via
 * getResourceCatalog() (concurrent callers share one in-flight request).
 *
 * Auth note (WO-ADM-RESOURCE-CATALOG-AUTH): the gameserver route requires any
 * authenticated user (`get_current_user`), so admin-only sessions (User +
 * AdminCredentials, no Player row) succeed the same as player sessions.
 * Network / auth failures still degrade to an empty list rather than crashing
 * or reintroducing stale mock data.
 *
 * `precious_metals` is seeded (WO-RES-PRECIOUS-METALS-SEED / #179–#180) as
 * `rare_material` — priced Secondary mining drop, not a core station
 * commodity. GET /api/v1/resources returns it with the rest of the active
 * catalog; EconomyDashboard maps `catalog[].name` directly. Local glyph
 * lives in DEFAULT_ICONS below (registry `icon` column stays placeholder).
 */
import { api } from '../utils/auth';

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

// Mirrors services/player-client/src/services/resourceCatalog.ts's DEFAULT_ICONS
// verbatim (WO-ARCH-RES-3C) so the two frontends cannot re-diverge on glyphs —
// the ruling (ore ⛏️, fuel_ore/fuel ⛽, generic 📦) is flagged to DECISIONS as
// part of the 3A entry. Icon is ALWAYS local — the registry's `icon` column
// is a documented placeholder, same reasoning as player-client.
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
  precious_metals: '🪙',
};

const GENERIC_ICON = '📦';

let cachedCatalog: ResourceCatalogEntry[] | null = null;
let inFlight: Promise<ResourceCatalogEntry[]> | null = null;
const listeners = new Set<() => void>();

/**
 * Fetch (or return the cached) resource catalog. Concurrent callers share
 * one in-flight request. A failed fetch is NOT cached, so the next caller
 * retries rather than being stuck on a transient error forever; callers
 * should treat rejection as "no catalog available" and degrade gracefully.
 */
export function getResourceCatalog(): Promise<ResourceCatalogEntry[]> {
  if (cachedCatalog) return Promise.resolve(cachedCatalog);
  if (!inFlight) {
    inFlight = api
      .get<ResourceCatalogEntry[]>('/api/v1/resources')
      .then((res) => {
        cachedCatalog = Array.isArray(res.data) ? res.data : [];
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

/** "quantum_shards" -> "Quantum Shards" — last-resort label for an unregistered key. */
const prettify = (key: string): string =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

/** Registry label (when loaded and known) -> prettified key. Never throws, never blank. */
export function resourceLabel(
  name: string,
  catalog: ResourceCatalogEntry[] | null = cachedCatalog
): string {
  return catalog?.find((r) => r.name === name)?.label || prettify(name);
}

/** Display glyph for a resource key — always local; see DEFAULT_ICONS note above. */
export function resourceIcon(name: string): string {
  return DEFAULT_ICONS[name] || GENERIC_ICON;
}
