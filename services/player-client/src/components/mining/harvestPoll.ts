/** LEG-2731 — async asteroid harvest poll helpers (sessionStorage + status gates). */

export const PENDING_HARVEST_STORAGE_KEY = 'sw2102_pending_harvest_id';

export const HARVEST_POLL_INTERVAL_MS = 2000;

/** GET /mining/harvest/{id} uses PENDING; POST uses in_progress until terminal. */
export function isTerminalHarvestStatus(status: string | undefined | null): boolean {
  if (!status) return false;
  const normalized = status.toUpperCase();
  return normalized !== 'PENDING' && normalized !== 'IN_PROGRESS';
}

export function formatHarvestCountdown(resolvesAt: string | undefined | null, nowMs: number): string | null {
  if (!resolvesAt) return null;
  const targetMs = new Date(resolvesAt).getTime();
  if (Number.isNaN(targetMs)) return null;
  const ms = targetMs - nowMs;
  if (ms <= 0) return 'completing…';
  const totalSec = Math.ceil(ms / 1000);
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  if (minutes > 0) return `~${minutes}m ${seconds}s remaining`;
  return `~${seconds}s remaining`;
}
