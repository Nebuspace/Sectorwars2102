import React from 'react';
import CockpitPanel from './CockpitPanel';
import EmpireResearchPanel from '../research/EmpireResearchPanel';

/**
 * Soft-ORDER invent=0 — Research HUD honesty surface (LEG-4076).
 * Re-exports densified EmpireResearchPanel load formatter (child owns API).
 */
export { formatEmpireResearchLoadError as formatResearchPanelError } from '../research/EmpireResearchPanel';

/**
 * ResearchPanel — the RESEARCH (flywheel) HUD instrument. Reuses
 * EmpireResearchPanel's empire-wide R&D cockpit verbatim inside a cockpit
 * panel. It is empire-level (not per-planet), but lives here beside the
 * citadel/grid panels — the home of the Citadel↔Research loop it surfaces.
 */
const ResearchPanel: React.FC = () => (
  <CockpitPanel title="Research" accent="#22d3ee" readout="⟳ flywheel">
    <EmpireResearchPanel />
  </CockpitPanel>
);

export default ResearchPanel;
