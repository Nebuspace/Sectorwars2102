import React from 'react';
import CockpitPanel from './CockpitPanel';
import GridManager from '../planetary/GridManager';

export interface GridPanelProps {
  planetId: string;
  playerCredits: number;
  /** Placed-building count for the header readout (x/9), when known. */
  placed?: number | null;
  capacity?: number | null;
  onUpdate: () => void;
}

/**
 * GridPanel — the GRID HUD instrument. Reuses GridManager's place /
 * decommission / catalog logic verbatim inside a cockpit panel. The grid IS
 * the primary action (it renders first inside the manager), so no extra modal
 * buttons are needed here.
 */
const GridPanel: React.FC<GridPanelProps> = ({ planetId, playerCredits, placed, capacity, onUpdate }) => (
  <CockpitPanel
    title="Grid"
    accent="#a78bfa"
    readout={typeof placed === 'number' ? `${placed}/${capacity ?? 9}` : 'x/9'}
  >
    <GridManager planetId={planetId} playerCredits={playerCredits} onUpdate={onUpdate} />
  </CockpitPanel>
);

export default GridPanel;
