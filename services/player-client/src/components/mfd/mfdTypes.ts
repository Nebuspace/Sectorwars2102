/**
 * mfdTypes — NEON15 MFD console framework frozen contracts.
 *
 * B1 implements these; B2/B3/B4 import them. Do not widen or rename
 * without re-freezing the contract.
 */

import type React from 'react';

export type MFDPageId =
  | 'vessel-status' | 'cargo' | 'turn-economy' | 'threat-readiness' | 'quantum-drive'
  | 'nav-position' | 'aria-terminal' | 'comms-crew' | 'reputation';

export type MFDFeatureStatus = 'shipped' | 'partial';

export type MFDAlertChannel = 'aria-event' | 'new-message' | 'autopilot-pause';

// Snapshot assembled ONCE in MFDConsole-level code and passed to
// predicates. Predicates are pure + synchronous. Pages do NOT receive
// it — they read hooks themselves.
export interface MFDSnapshot {
  currentShip: unknown | null;     // GameContext.currentShip
  playerState: unknown | null;     // GameContext.playerState
  currentSector: unknown | null;   // GameContext.currentSector
  isConnected: boolean;            // WebSocketContext.isConnected
}

export interface MFDPageDef {
  id: MFDPageId;
  title: string;          // page header line, e.g. 'VESSEL STATUS'
  softLabel: string;      // <=5 chars softkey label, e.g. 'STAT'
  accent: string;         // hex from the NAV_ITEMS palette
  status: MFDFeatureStatus;  // drives the honesty chip in the page header
  Component: React.LazyExoticComponent<React.ComponentType>;
  /** false => softkey rendered disabled. Throw => treated as false. */
  available?: (s: MFDSnapshot) => boolean;
  /** true => softkey not rendered at all (conditional pages like quantum-drive). */
  hidden?: (s: MFDSnapshot) => boolean;
  alertChannel?: MFDAlertChannel;
}

export interface MFDScreenConfig {
  screenId: 'sidebar-a' | 'sidebar-b';
  systemLabel: string;            // bezel corner, 'MFD-A' / 'MFD-B'
  pageIds: MFDPageId[];           // ordered, drives softkey order; max 5
  defaultPageId: MFDPageId;
}

export interface MFDContextValue {
  activeFor: (screenId: string) => MFDPageId | undefined;
  selectPage: (screenId: string, pageId: MFDPageId) => void;
  hasAlert: (pageId: MFDPageId) => boolean;
  raiseAlert: (channel: MFDAlertChannel) => void;  // badges matching pages not visible
  clearAlert: (pageId: MFDPageId) => void;
}
