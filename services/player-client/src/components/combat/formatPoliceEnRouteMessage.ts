import type { PendingEngagementSummary } from '../../services/pendingEngagementApi';

/** Canon-facing copy: "Marshal Vance is en route — 2 turns to arrival". */
export function formatPoliceEnRouteMessage(
  summary: PendingEngagementSummary
): string {
  const name =
    summary.officer_names[0] ??
    summary.squad[0] ??
    'Law enforcement';
  const turns = summary.turns_to_arrival;
  const turnWord = turns === 1 ? 'turn' : 'turns';
  return `${name} is en route — ${turns} ${turnWord} to arrival`;
}
