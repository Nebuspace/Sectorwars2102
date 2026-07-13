import React, { useEffect, useState } from 'react';
import DeckPageTabs from '../cockpit/DeckPageTabs';
import TacticalTargetPage, { type TacticalContact } from './pages/TacticalTargetPage';
import TacticalThreatPage from './pages/TacticalThreatPage';
import { useTacticalPageRequest } from '../../hooks/useDeckNav';
import './tactical-monitor.css';

/**
 * TacticalMonitor — the cockpit's TACTICAL deck-monitor: a thin
 * DeckPageTabs host over exactly two pages (WO-UI2-DECK-RECONCILE, §05:
 * TACTICAL [TARGET · THREAT]):
 *   TARGET  — CommsMailbox's CONTACTS list, enhanced (TacticalTargetPage).
 *   THREAT  — mfd/pages/ThreatPage's law/mines/hazard (TacticalThreatPage).
 *
 * PRIOR ROLE, NOW RETIRED: an earlier WO-UI2-TACTICAL-MONITOR pass had
 * this file fetch + render a STATIC known-graph threat-band rollup (GET
 * /nav/threat) as a standalone 4th deck-monitor — a page §05 never
 * specified. That fetch has ZERO callers left once this file stopped
 * calling it (nav.py's endpoint itself is untouched and simply goes
 * dormant). TACTICAL is back to being the 3rd of exactly 3 flight
 * deck-monitors, per §05.
 *
 * WO-UI1-CHROME-COMPLETE (item 6, MINIMAL shared-nav wiring, flagged in
 * the STATUS report): the annunciator's LAW/THREAT lamps (Annunciator.tsx,
 * mounted in GameLayout.tsx) click-through to this monitor's own softkey
 * page. There is no existing shared nav context between GameLayout and the
 * deck (TacticalMonitor's `page` is a local useState, deliberately —
 * WO-UI2-DECK-RECONCILE's "one selection grammar" convention), so this
 * subscribes to the small module-level bus in services/deckNavBus.ts
 * rather than threading a prop through GameDashboard (out of this WO's
 * scope, and would restructure the deck for a single consumer).
 */

export type { TacticalContact };

interface TacticalMonitorProps {
  contacts: TacticalContact[];
  /** ship_id of the currently selected contact (spotlit in the viewport). */
  selectedShipId?: string | null;
  /** Clicking a contact name selects its ship in the cockpit viewport. */
  onSelectContact?: (contact: TacticalContact | null) => void;
}

const TacticalMonitor: React.FC<TacticalMonitorProps> = ({ contacts, selectedShipId, onSelectContact }) => {
  const [page, setPage] = useState<'target' | 'threat'>('target');

  // Annunciator LAW→THREAT / THREAT→TARGET click-through (see the
  // deckNavBus import doc-comment above). Fires on every distinct
  // requestId, including a repeat click on the page already shown, and
  // picks up a request that latched while this monitor was unmounted
  // (docked/landed — TACTICAL only renders in flight mode).
  const tacticalPageRequest = useTacticalPageRequest();
  useEffect(() => {
    if (tacticalPageRequest) setPage(tacticalPageRequest.page);
  }, [tacticalPageRequest]);

  return (
    <>
      <div className="screen-hud-header tactical-header-with-modes">
        <span>TACTICAL</span>
        <DeckPageTabs
          pages={[
            { id: 'target', label: 'TARGET' },
            { id: 'threat', label: 'THREAT' },
          ]}
          activeId={page}
          onSelect={(id) => setPage(id as 'target' | 'threat')}
          ariaLabel="TACTICAL display mode"
          accent="#FF8800"
          idBase="tactical"
        />
      </div>
      <div
        className="screen-hud-content"
        role="tabpanel"
        id={`tactical-panel-${page}`}
        aria-labelledby={`tactical-tab-${page}`}
      >
        {page === 'target' ? (
          <TacticalTargetPage contacts={contacts} selectedShipId={selectedShipId} onSelectContact={onSelectContact} />
        ) : (
          <TacticalThreatPage />
        )}
      </div>
    </>
  );
};

export default TacticalMonitor;
