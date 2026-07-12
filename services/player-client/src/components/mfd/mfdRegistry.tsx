/**
 * mfdRegistry — the six MFD page definitions, decoupled from screens.
 *
 * Pages are lazy chunks so a faulting or heavy page never taxes the
 * console shell. Accents reuse the NAV_ITEMS palette (Law-5 system
 * colors). Predicates receive the memoized MFDSnapshot and must stay
 * pure + synchronous.
 *
 * WO-UI2-DECK-RECONCILE dropped four entries down to the ratified MFD
 * slate (canon §05: "A: STAT · CRGO · QTM — B: POS · COMM"):
 *   - threat-readiness  → MOVED to the deck TACTICAL monitor's [THREAT] page
 *   - salvage           → MOVED to the deck SOLAR SYSTEM monitor's [SALVAGE] page
 *   - turn-economy, reputation → already-dead entries (WO-PLAYERINFO id=147
 *     dropped their sidebar softkeys; nothing referenced them since)
 * The ReputationPage and SalvagePage/ThreatPage/TurnEconomyPage COMPONENTS
 * are untouched — only the registry entries are gone. ReputationPage is
 * still reused directly by components/player/PlayerInfo.tsx's dossier
 * reputation tab.
 */

import React from 'react';
import type {
  MFDAlertChannel,
  MFDPageDef,
  MFDPageId,
  MFDSnapshot,
} from './mfdTypes';

// GameContext gates quantumStatus on currentShip.type === 'WARP_JUMPER';
// the hidden predicate mirrors that exact check (snapshot carries the
// ship as unknown, so narrow before reading).
const isWarpJumper = (snapshot: MFDSnapshot): boolean => {
  const ship = snapshot.currentShip;
  if (ship === null || typeof ship !== 'object') return false;
  return (ship as { type?: unknown }).type === 'WARP_JUMPER';
};

export const MFD_PAGES: Record<MFDPageId, MFDPageDef> = {
  'vessel-status': {
    id: 'vessel-status',
    title: 'VESSEL STATUS',
    softLabel: 'STAT',
    accent: '#00D9FF',
    status: 'shipped',
    Component: React.lazy(() => import('./pages/VesselPage')),
  },
  'cargo': {
    id: 'cargo',
    title: 'CARGO BAY',
    softLabel: 'CRGO',
    accent: '#9EC5FF',
    status: 'shipped',
    Component: React.lazy(() => import('./pages/CargoPage')),
  },
  'quantum-drive': {
    id: 'quantum-drive',
    title: 'QUANTUM DRIVE',
    softLabel: 'QTM',
    accent: '#7B2FFF',
    status: 'partial',
    Component: React.lazy(() => import('./pages/QuantumPage')),
    hidden: (s) => !isWarpJumper(s),
  },
  'nav-position': {
    id: 'nav-position',
    title: 'NAV / POSITION',
    softLabel: 'NAV',
    accent: '#00D9FF',
    status: 'shipped',
    Component: React.lazy(() => import('./pages/NavPositionPage')),
    // Autopilot pauses are a navigation event; ARIA terminal already
    // carries the aria-event channel (one channel per page).
    alertChannel: 'autopilot-pause',
  },
  'aria-terminal': {
    id: 'aria-terminal',
    title: 'ARIA TERMINAL',
    softLabel: 'ARIA',
    accent: '#7B2FFF',
    status: 'shipped',
    Component: React.lazy(() => import('./pages/AriaTerminalPage')),
    alertChannel: 'aria-event',
  },
  'comms-crew': {
    id: 'comms-crew',
    title: 'COMMS / CREW',
    softLabel: 'COMM',
    accent: '#00FF7F',
    status: 'shipped',
    Component: React.lazy(() => import('./pages/CommsCrewPage')),
    alertChannel: 'new-message',
  },
};

export const getPageDef = (id: MFDPageId): MFDPageDef => MFD_PAGES[id];

export const pagesForChannel = (channel: MFDAlertChannel): MFDPageId[] =>
  (Object.values(MFD_PAGES) as MFDPageDef[])
    .filter((def) => def.alertChannel === channel)
    .map((def) => def.id);

/** Contract: a throwing `available` predicate is treated as false. */
export const isPageAvailable = (def: MFDPageDef, snapshot: MFDSnapshot): boolean => {
  if (def.available === undefined) return true;
  try {
    return def.available(snapshot);
  } catch {
    return false;
  }
};

/** A throwing `hidden` predicate keeps the page visible (fail open). */
export const isPageHidden = (def: MFDPageDef, snapshot: MFDSnapshot): boolean => {
  if (def.hidden === undefined) return false;
  try {
    return def.hidden(snapshot);
  } catch {
    return false;
  }
};
