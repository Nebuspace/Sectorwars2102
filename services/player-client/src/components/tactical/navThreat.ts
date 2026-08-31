import type { NavThreatBand, NavThreatEntry } from '../../services/api';

/** Hide fetch TypeError noise — stable fallback for GET /nav/threat consumers. */
export function formatNavThreatError(err: unknown, fallback = 'Threat data unavailable — check your connection.'): string {
  if (err instanceof TypeError) return fallback;
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && message.trim() && !/^API Error: \d+$/.test(message.trim())) {
    return message.trim();
  }
  return fallback;
}

export type NavThreatMap = Record<number, NavThreatEntry>;

export function navThreatEntriesToMap(entries: NavThreatEntry[]): NavThreatMap {
  const map: NavThreatMap = {};
  for (const row of entries) {
    if (row && typeof row.sector_id === 'number') {
      map[row.sector_id] = row;
    }
  }
  return map;
}

export const NAV_THREAT_BAND_CLASS: Record<NavThreatBand, string> = {
  CLEAR: 'nav-threat-clear',
  CAUTION: 'nav-threat-caution',
  HOSTILE: 'nav-threat-hostile',
  LETHAL: 'nav-threat-lethal',
};
