import React, { useState } from 'react';
import DeckPageTabs from '../cockpit/DeckPageTabs';
import TacticalTargetPage, { type TacticalContact } from './pages/TacticalTargetPage';
import TacticalThreatPage from './pages/TacticalThreatPage';
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
