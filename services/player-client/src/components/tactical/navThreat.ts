import type { NavThreatBand, NavThreatEntry } from '../../services/api';

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Hide fetch TypeError / network-collapse noise — stable fallback for GET /nav/threat consumers. */
export function formatNavThreatError(err: unknown, fallback = 'Threat data unavailable — check your connection.'): string {
  if (err instanceof TypeError) return fallback;
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && message.trim() && !/^API Error: \d+$/.test(message.trim())) {
    if (isNetworkCollapseMessage(message)) return fallback;
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
