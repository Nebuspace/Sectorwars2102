/**
 * Combat HUD preview helpers (LEG-305).
 *
 * Weapon defaults: canon combat.md § Default weapon by ship.
 * Ship attack_turn_cost: tip-mirror of ship_specifications_seeder.py @
 * origin/feat 0ea07808 — client has no ShipSpecification catalog; presence
 * omits attack_turn_cost. Prefer a future GS presence/catalog field over
 * keeping this table forever.
 * Planet/port turn costs: canon combat.md default table (3).
 */

export type CombatEngageKind = 'ship' | 'planet' | 'port';

/** Canon SHIP_DEFAULT_WEAPONS (combat.md + combat_service.py). */
const SHIP_DEFAULT_WEAPONS: Record<string, string> = {
  ESCAPE_POD: 'Laser',
  LIGHT_FREIGHTER: 'Laser',
  CARGO_HAULER: 'Laser',
  FAST_COURIER: 'Laser',
  CITIZEN_CLIPPER: 'Laser',
  SCOUT_SHIP: 'EMP',
  COLONY_SHIP: 'Laser',
  DEFENDER: 'Plasma',
  CARRIER: 'Missile',
  WARP_JUMPER: 'Plasma',
  NPC_MARSHAL_INTERDICTOR: 'Laser',
  NPC_SENTINEL_INTERDICTOR: 'Plasma',
};

/**
 * Tip-mirrored attack_turn_cost by ShipType (ship_specifications_seeder).
 * Values are NOT invented — they match tip seeder; unknown types → null.
 */
const ATTACK_TURN_COST_BY_TYPE: Record<string, number> = {
  ESCAPE_POD: 10000,
  LIGHT_FREIGHTER: 12,
  CARGO_HAULER: 20,
  FAST_COURIER: 8,
  CITIZEN_CLIPPER: 8,
  SCOUT_SHIP: 5,
  COLONY_SHIP: 35,
  DEFENDER: 18,
  CARRIER: 45,
  WARP_JUMPER: 100,
  NPC_MARSHAL_INTERDICTOR: 18,
  NPC_SENTINEL_INTERDICTOR: 18,
};

export function normalizeShipType(raw: unknown): string | null {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (!s || s === 'None') return null;
  return s.toUpperCase().replace(/[\s-]+/g, '_');
}

export function defaultWeaponForShipType(shipType: unknown): string {
  const key = normalizeShipType(shipType);
  if (!key) return 'Laser';
  return SHIP_DEFAULT_WEAPONS[key] ?? 'Laser';
}

/** Canon: ship attacks use defender attack_turn_cost, min 2. */
export function previewTurnCost(opts: {
  targetType: CombatEngageKind;
  shipType?: unknown;
  /** When tip/presence eventually exposes the field, prefer it. */
  attackTurnCost?: number | null;
}): number | null {
  if (opts.targetType === 'planet' || opts.targetType === 'port') {
    return 3;
  }
  if (typeof opts.attackTurnCost === 'number' && Number.isFinite(opts.attackTurnCost)) {
    return Math.max(2, Math.floor(opts.attackTurnCost));
  }
  const key = normalizeShipType(opts.shipType);
  if (!key) return null;
  const fromTable = ATTACK_TURN_COST_BY_TYPE[key];
  if (typeof fromTable !== 'number') return null;
  return Math.max(2, fromTable);
}

export function formatCombatGauge(current: unknown, max: unknown): string {
  const c = typeof current === 'number' && Number.isFinite(current) ? current : null;
  const m = typeof max === 'number' && Number.isFinite(max) ? max : null;
  if (c !== null && m !== null) return `${c} / ${m}`;
  if (c !== null) return `${c}`;
  return '—';
}

export function cargoUsedAndCapacity(ship: {
  cargo?: unknown;
  cargo_capacity?: number;
} | null | undefined): { used: number | null; capacity: number | null } {
  if (!ship) return { used: null, capacity: null };
  const cargo =
    ship.cargo && typeof ship.cargo === 'object' && !Array.isArray(ship.cargo)
      ? (ship.cargo as Record<string, unknown>)
      : {};
  let used: number | null = null;
  if (typeof cargo.used === 'number' && Number.isFinite(cargo.used)) {
    used = cargo.used;
  } else if (cargo.contents && typeof cargo.contents === 'object') {
    used = Object.values(cargo.contents as Record<string, unknown>).reduce<number>(
      (sum, v) => sum + (Number(v) || 0),
      0,
    );
  } else {
    const skip = new Set(['capacity', 'used', 'contents']);
    const vals = Object.entries(cargo).filter(
      ([k, v]) => !skip.has(k) && typeof v === 'number',
    );
    if (vals.length > 0) {
      used = vals.reduce((sum, [, v]) => sum + (v as number), 0);
    }
  }
  const capacity =
    typeof ship.cargo_capacity === 'number' && Number.isFinite(ship.cargo_capacity)
      ? ship.cargo_capacity
      : typeof cargo.capacity === 'number' && Number.isFinite(cargo.capacity)
        ? (cargo.capacity as number)
        : null;
  return { used, capacity };
}
