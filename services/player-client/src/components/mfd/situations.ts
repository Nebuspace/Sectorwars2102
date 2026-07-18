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
  // 'sidebar-a-folded' (WO-UI1-CHROME-COMPLETE mid-panel fold) is the
  // same physical MFD-A screen wearing a wider pageIds list — same
  // docked/landed-aware default as 'sidebar-a'.
  if (screenId === 'sidebar-a' || screenId === 'sidebar-a-folded') {
    return isDockedOrLanded(snapshot.playerState) ? 'cargo' : 'vessel-status';
  }
  // sidebar-b: ARIA is absorbed into the teleprinter (no longer an MFD-B
  // page — WO-UI1-CHROME-COMPLETE), so POS is the natural default.
  return 'nav-position';
};
