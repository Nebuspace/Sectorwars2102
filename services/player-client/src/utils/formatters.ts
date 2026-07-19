/**
 * Shared formatting utilities for display values.
 */

/**
 * The in-universe credits glyph (₡, U+20A1). Use EVERYWHERE in place of
 * 'CRED' / 'credits' / 'cr' so money reads consistently across the cockpit
 * (WO-PLAYERINFO id=148). Swap here to change it globally.
 */
export const CREDITS_SYMBOL = '₡';

/**
 * Format a credit amount with thousands grouping prefixed by the ₡ glyph,
 * e.g. 1234567 → "₡1,234,567". Nullish / non-finite → "₡0".
 */
export const formatCredits = (amount: number | null | undefined): string => {
  const n = typeof amount === 'number' && Number.isFinite(amount) ? amount : 0;
  return `${CREDITS_SYMBOL}${n.toLocaleString()}`;
};

/**
 * Format enum-style ship type names like "LIGHT_FREIGHTER" to "Light Freighter".
 * Should only be used for ship TYPE enums, not user-facing ship names.
 */
export const formatShipType = (type: string): string => {
  return type
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase());
};

/**
 * Format a sector/move `region_type` enum value ("terran_space",
 * "central_nexus", "player_owned" — models/region.py RegionType) to its
 * display form ("Terran Space"). WO-T1D-LANEB: this is the ONLY sanctioned
 * derivation for the glass locrow region chip — never parse it out of
 * `region_name` (rejected 2026-07-13: the dev-seeded name's "<galaxy> —
 * <Type>" em-dash suffix, e.g. "Stage2 Genesis R4 — Terran Space", is a
 * bang-import naming convention, not a guaranteed prod format; parsing it
 * is fragile). Nullish/empty input returns null so the caller can render
 * nothing instead of a guess.
 */
export const formatRegionType = (regionType: string | null | undefined): string | null => {
  if (!regionType) return null;
  return regionType
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase());
};
