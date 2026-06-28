/**
 * situations — situation-derived INITIAL page defaults (Design B graft).
 *
 * Consulted ONCE per screen, at hydration time, when no valid persisted
 * selection exists. Never used for live switching — the pilot's manual
 * selection always wins after that.
 */

import type { MFDPageId, MFDScreenConfig, MFDSnapshot } from './mfdTypes';

// PlayerState.is_docked / is_landed (GameContext) — narrowed defensively
// because the snapshot deliberately carries playerState as unknown.
const isDockedOrLanded = (playerState: unknown): boolean => {
  if (playerState === null || typeof playerState !== 'object') return false;
  const ps = playerState as { is_docked?: unknown; is_landed?: unknown };
  return ps.is_docked === true || ps.is_landed === true;
};

export const deriveInitialPage = (
  screenId: MFDScreenConfig['screenId'],
  snapshot: MFDSnapshot,
): MFDPageId => {
  if (screenId === 'sidebar-a') {
    return isDockedOrLanded(snapshot.playerState) ? 'cargo' : 'vessel-status';
  }
  // sidebar-b: ARIA presence survives the bottom-strip removal
  return 'aria-terminal';
};
