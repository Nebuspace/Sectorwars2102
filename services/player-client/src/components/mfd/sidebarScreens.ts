/**
 * sidebarScreens — frozen screen configs for the two left-console MFDs.
 */

import type { MFDScreenConfig } from './mfdTypes';

// WO-PLAYERINFO id=147: dropped the TURN (turn-economy) and REP (reputation)
// softkeys — turns/regen/credits now live in the always-on HUD (id=145),
// reputation in the PlayerInfo view (PLY, id=144), and TURN's autopilot
// hop-costs are rehomed into NAV (NavPositionPage).
//
// WO-UI2-DECK-RECONCILE dropped THRT (threat-readiness → deck TACTICAL
// [THREAT]) and SALV (salvage → deck SOLAR SYSTEM [SALVAGE]), landing the
// ratified MFD slate (canon §05): A: STAT · CRGO · QTM. All four dropped
// page components stay on disk; only their registry entries + sidebar
// softkeys are gone (mfdRegistry.tsx).
export const SIDEBAR_A: MFDScreenConfig = {
  screenId: 'sidebar-a', systemLabel: 'MFD-A', defaultPageId: 'vessel-status',
  pageIds: ['vessel-status', 'cargo', 'quantum-drive'],
};

// WO-UI1-CHROME-COMPLETE dropped ARIA — absorbed into the teleprinter
// (components/aria/Teleprinter.tsx carries the ADR-0072 grammar + free-
// chat directly now). MFD-B slate == [POS, COMM] per canon §05 L578.
export const SIDEBAR_B: MFDScreenConfig = {
  screenId: 'sidebar-b', systemLabel: 'MFD-B', defaultPageId: 'nav-position',
  pageIds: ['nav-position', 'comms-crew'],
};

// The teleprinter's mid-panel display mode folds MFD-B's pages into
// MFD-A's rail (canon §05 L624: "MFD-B folds its pages into MFD-A's rail
// (5-key cap respected)") so the teleprinter's own expanded body can take
// MFD-B's screen real estate. A DISTINCT screenId (not a wider `SIDEBAR_A`
// config under the SAME 'sidebar-a' id) is deliberate: MFDContext's
// REGISTER_SCREEN reducer case no-ops on a screenId that's already
// registered (StrictMode/remount guard, MFDContext.tsx), so re-rendering
// 'sidebar-a' with a wider pageIds array would leave the reducer's
// registered pageIds frozen at the ORIGINAL 3 — selecting NAV/COMM would
// silently no-op (SELECT_PAGE validates against the registered pageIds,
// not the live config prop). GameLayout swaps which config it renders
// (this one vs. the SIDEBAR_A/SIDEBAR_B pair) rather than mutating either
// in place, so each screenId registers cleanly on first use. Exactly 5
// pages == the MAX_SOFTKEYS cap (MFDSoftkeyRail.tsx); quantum-drive's own
// `hidden` predicate (WJ-gated) keeps non-WARP_JUMPER ships at 4.
export const SIDEBAR_A_FOLDED: MFDScreenConfig = {
  screenId: 'sidebar-a-folded', systemLabel: 'MFD-A', defaultPageId: 'vessel-status',
  pageIds: ['vessel-status', 'cargo', 'quantum-drive', 'nav-position', 'comms-crew'],
};
