/**
 * sidebarScreens — frozen screen configs for the two left-console MFDs.
 */

import type { MFDScreenConfig } from './mfdTypes';

// WO-PLAYERINFO id=147: dropped the TURN (turn-economy) and REP (reputation)
// softkeys — turns/regen/credits now live in the always-on HUD (id=145),
// reputation in the PlayerInfo view (PLY, id=144), and TURN's autopilot
// hop-costs are rehomed into NAV (NavPositionPage). The page components stay
// registered; they're just no longer surfaced as sidebar softkeys.
export const SIDEBAR_A: MFDScreenConfig = {
  screenId: 'sidebar-a', systemLabel: 'MFD-A', defaultPageId: 'vessel-status',
  // WO-CMB-SALVAGE-LOOP-1: 'salvage' joins the cargo/threat cluster
  // (post-combat loot recovery) — lands this screen at its documented
  // max of 5. Further additions belong on SIDEBAR_B.
  pageIds: ['vessel-status', 'cargo', 'threat-readiness', 'quantum-drive', 'salvage'],
};

export const SIDEBAR_B: MFDScreenConfig = {
  screenId: 'sidebar-b', systemLabel: 'MFD-B', defaultPageId: 'aria-terminal',
  pageIds: ['nav-position', 'aria-terminal', 'comms-crew'],
};
