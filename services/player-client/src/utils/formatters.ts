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
