/**
 * mfdTypes — NEON15 MFD console framework frozen contracts.
 *
 * B1 implements these; B2/B3/B4 import them. Do not widen or rename
 * without re-freezing the contract.
 *
 * Re-frozen at 5 pages (WO-UI1-CHROME-COMPLETE, cockpit-redesign-v10
 * §05 L578): dropped 'aria-terminal' — ARIA is absorbed into the
 * teleprinter (components/aria/Teleprinter.tsx), which now carries the
 * ADR-0072 grammar + free-chat directly, landing the ratified slate
 * "A: STAT · CRGO · QTM — B: POS · COMM" (no ARIA tab on MFD-B).
 * AriaTerminalPage.tsx is DELETED (WO-UI5-RETIREMENT+GLASS — zero
 * remaining consumers), same retirement pattern as ThreatPage/SalvagePage
 * (WO-UI2-DECK-RECONCILE, also deleted). Previously
 * re-frozen at 6 pages (WO-UI2-DECK-RECONCILE): dropped 'turn-economy' +
 * 'reputation' (already-dead sidebar entries, WO-PLAYERINFO id=147) and
 * 'threat-readiness' + 'salvage' (MOVED to the deck TACTICAL / SOLAR
 * SYSTEM monitors). mfdRegistry.tsx's `Record<MFDPageId, MFDPageDef>`
 * requires every union member to have an entry, so shrinking the
 * registry forces this shrink too — same mechanical coupling each time.
 * Widening path unchanged: add the id here, update this note, done.
 */

import type React from 'react';

export type MFDPageId =
  | 'vessel-status' | 'cargo' | 'quantum-drive'
  | 'nav-position' | 'comms-crew';

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
  // 'sidebar-a-folded' (WO-UI1-CHROME-COMPLETE, sidebarScreens.ts) is the
  // teleprinter mid-panel's MFD-B→MFD-A fold target — a DISTINCT screenId
  // from 'sidebar-a' so MFDContext's registration guard can't freeze it
  // at the unfolded pageIds (see SIDEBAR_A_FOLDED's own doc-comment).
  screenId: 'sidebar-a' | 'sidebar-b' | 'sidebar-a-folded';
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
