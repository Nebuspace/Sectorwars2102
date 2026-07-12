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

export const SIDEBAR_B: MFDScreenConfig = {
  screenId: 'sidebar-b', systemLabel: 'MFD-B', defaultPageId: 'aria-terminal',
  pageIds: ['nav-position', 'aria-terminal', 'comms-crew'],
};
