/**
 * sidebarScreens — frozen screen configs for the two left-console MFDs.
 */

import type { MFDScreenConfig } from './mfdTypes';

export const SIDEBAR_A: MFDScreenConfig = {
  screenId: 'sidebar-a', systemLabel: 'MFD-A', defaultPageId: 'vessel-status',
  pageIds: ['vessel-status', 'cargo', 'turn-economy', 'threat-readiness', 'quantum-drive'],
};

export const SIDEBAR_B: MFDScreenConfig = {
  screenId: 'sidebar-b', systemLabel: 'MFD-B', defaultPageId: 'aria-terminal',
  pageIds: ['nav-position', 'aria-terminal', 'comms-crew', 'reputation'],
};
