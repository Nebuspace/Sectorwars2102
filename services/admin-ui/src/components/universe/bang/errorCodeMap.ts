/**
 * Map sw2102-bang validator error codes (B-NNN) to i18n keys under
 * `admin.bang.errors.*`. The codes themselves are stable identifiers
 * emitted by bang's Phase-13 validator (see sw2102-bang `src/rules.ts`).
 *
 * Codes not in this map fall back to the generic "unknown" key plus
 * the raw `code` and `message` from the warning, so the operator
 * always has something actionable on screen.
 */

export const BANG_ERROR_CODES: Record<string, string> = {
  // Topology / connectivity
  'B-010': 'admin.bang.errors.isolatedSector',
  'B-020': 'admin.bang.errors.maxWarpsExceeded',
  'B-030': 'admin.bang.errors.oneWayWarpUnreachable',
  'B-040': 'admin.bang.errors.fragmented',
  'B-050': 'admin.bang.errors.duplicateSectorNumber',

  // Economy / commodity coverage
  'B-200': 'admin.bang.errors.commodityCoverage',
  'B-210': 'admin.bang.errors.priceRangeOutOfBand',
  'B-220': 'admin.bang.errors.stationDensityLow',

  // Formations / planets
  'B-300': 'admin.bang.errors.formationCountMismatch',
  'B-310': 'admin.bang.errors.planetWithoutHost',
  'B-320': 'admin.bang.errors.nebulaOverlap',

  // Emissions / heuristics
  'B-400': 'admin.bang.errors.emissionUndertarget',
  'B-410': 'admin.bang.errors.emissionOvertarget',
  'B-420': 'admin.bang.errors.heuristicFallback',

  // Rescue / topology repair
  'B-500': 'admin.bang.errors.topologyRescue',
  'B-510': 'admin.bang.errors.bubbleFallback',
};

/** Warning category → CSS class suffix (color-coded in the log panel). */
export const WARNING_CATEGORY_CLASS: Record<string, string> = {
  TOPOLOGY_RESCUE: 'topology-rescue',
  EMISSION_UNDERTARGET: 'emission-undertarget',
  EMISSION_OVERTARGET: 'emission-overtarget',
  HEURISTIC_FALLBACK: 'heuristic-fallback',
  COMMODITY_COVERAGE: 'commodity-coverage',
  BUBBLE_FALLBACK: 'bubble-fallback',
  VALIDATOR_FAILURE: 'validator-failure',
};

/**
 * Resolve the i18n key for a given bang `B-NNN` code. Returns the generic
 * unknown-error key if the code isn't mapped — the caller is expected to
 * also surface the raw `code` + `message` for unknown codes.
 */
export function i18nKeyForBangCode(code: string): string {
  return BANG_ERROR_CODES[code] ?? 'admin.bang.errors.unknown';
}

/** Resolve a CSS class suffix for a given warning category. */
export function classForWarningCategory(category: string): string {
  return WARNING_CATEGORY_CLASS[category] ?? 'category-default';
}
