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

function httpStatusNavThreat(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Hide fetch TypeError / network-collapse noise — stable fallback for GET /nav/threat consumers (LEG-4101). */
export function formatNavThreatError(err: unknown, fallback = 'Threat data unavailable — check your connection.'): string {
  const status = httpStatusNavThreat(err);
  const message = err instanceof Error ? err.message : undefined;
  const detail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message)
      ? message.trim()
      : undefined;

  if (status === 403) {
    if (detail) return detail;
    return 'You do not have permission to load threat data.';
  }
  if (status === 429) {
    return 'Threat data rate limit exceeded — wait a moment and try again.';
  }

  if (err instanceof TypeError) return fallback;
  if (detail) return detail;
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
