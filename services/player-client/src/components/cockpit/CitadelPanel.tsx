import React from 'react';
import CockpitPanel from './CockpitPanel';
import CitadelManager from '../planetary/CitadelManager';

export interface CitadelPanelProps {
  planetId: string;
  playerCredits: number;
  stationedDrones?: number;
  /** Citadel level for the glanceable header readout (from the landed poll). */
  citadelLevel?: number | null;
  /** True while this colony is under siege — flag it in the header. */
  underSiege?: boolean;
  onUpdate: () => void;
  /** Open the Buildings (defense building) modal. */
  onOpenBuildings: () => void;
  /** Open the Defense configuration modal. */
  onOpenDefense: () => void;
  /** Open the Siege status monitor modal (only meaningful under siege). */
  onOpenSiege: () => void;
}

/**
 * CitadelPanel — the CITADEL HUD instrument. Reuses CitadelManager's full
 * ladder/vault/upgrade logic verbatim (the same component the COLONIES screen
 * mounts) inside a cockpit panel, and exposes the Buildings / Defense / Siege
 * modals from its action row.
 */
const CitadelPanel: React.FC<CitadelPanelProps> = ({
  planetId,
  playerCredits,
  stationedDrones,
  citadelLevel,
  underSiege,
  onUpdate,
  onOpenBuildings,
  onOpenDefense,
  onOpenSiege,
}) => (
  <CockpitPanel
    title="Citadel"
    accent="#fbbf24"
    readout={
      <>
        {typeof citadelLevel === 'number' ? `Lv ${citadelLevel}` : 'Lv —'}
        {underSiege && <span className="cp-siege-flag"> · ⚠ SIEGE</span>}
      </>
    }
  >
    <CitadelManager
      planetId={planetId}
      playerCredits={playerCredits}
      stationedDrones={stationedDrones}
      onUpdate={onUpdate}
    />
    <div className="cp-actions">
      <button type="button" className="cp-action-btn" onClick={onOpenBuildings} title="Upgrade planetary buildings">
        🔨 Buildings
      </button>
      <button type="button" className="cp-action-btn" onClick={onOpenDefense} title="Configure turrets, shields, and drones">
        🛡️ Defenses
      </button>
      <button
        type="button"
        className={`cp-action-btn${underSiege ? ' danger' : ''}`}
        onClick={onOpenSiege}
        title={underSiege ? 'View active siege status' : 'Siege status monitor'}
      >
        🚨 Siege
      </button>
    </div>
  </CockpitPanel>
);

export default CitadelPanel;
