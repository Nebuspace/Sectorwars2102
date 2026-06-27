import React from 'react';
import CockpitPanel from './CockpitPanel';
import TerraformingPanel from '../planetary/TerraformingPanel';

export interface TerraformPanelProps {
  planetId: string;
  planetType?: string | null;
  playerCredits: number;
  habitabilityScore?: number | null;
  onUpdate: () => void;
}

/**
 * TerraformPanel — the TERRAFORM HUD instrument. Reuses TerraformingPanel's
 * start / cancel / biome-reclass logic verbatim inside a cockpit panel. The
 * header shows current habitability as the glanceable points-out-of-100 readout
 * (canon: habitability is an integer 0–100, not a percentage), kept consistent
 * with the "/100" form used inside TerraformingPanel.
 */
const TerraformPanel: React.FC<TerraformPanelProps> = ({
  planetId,
  planetType,
  playerCredits,
  habitabilityScore,
  onUpdate,
}) => (
  <CockpitPanel
    title="Terraform"
    accent="#34d399"
    readout={typeof habitabilityScore === 'number' ? `${Math.round(habitabilityScore)}/100` : '—'}
  >
    <TerraformingPanel
      planetId={planetId}
      planetType={planetType}
      playerCredits={playerCredits}
      habitabilityScore={habitabilityScore}
      onUpdate={onUpdate}
    />
  </CockpitPanel>
);

export default TerraformPanel;
