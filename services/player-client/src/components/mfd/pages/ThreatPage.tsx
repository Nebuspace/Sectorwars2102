/**
 * THREAT READINESS — MFD-A page (NEON15, zone B2). Status: partial.
 *
 * Passive readout only — no bounty fetch in v1 per contract.
 * Field provenance (verified):
 *   currentSector.{hazard_level,radiation_level,type} — Sector interface
 *   playerState.{defense_drones,attack_drones}        — PlayerState interface
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#FF4D6D';

const ThreatPage: React.FC = () => {
  const { currentSector, playerState } = useGame();

  if (!currentSector && !playerState) {
    return (
      <>
        <MFDPageHeader title="THREAT READINESS" accent={ACCENT} status="partial" />
        <MFDPageBody scrollKey="threat-readiness">
          <MFDInsufficient />
        </MFDPageBody>
      </>
    );
  }

  const hazard = currentSector ? currentSector.hazard_level : null;

  return (
    <>
      <MFDPageHeader title="THREAT READINESS" accent={ACCENT} status="partial" />
      <MFDPageBody scrollKey="threat-readiness">
        <div className="mfd-page-fields">
          <MFDField
            label="HAZARD LVL"
            value={hazard ?? '—'}
            accent={(hazard ?? 0) > 0}
          />
          <MFDField
            label="RADIATION"
            value={currentSector ? currentSector.radiation_level : '—'}
          />
          <MFDField
            label="SECTOR TYPE"
            value={currentSector?.type ? currentSector.type.toUpperCase() : '—'}
          />
          <MFDField
            label="DEF DRONES"
            value={playerState ? playerState.defense_drones : '—'}
          />
          <MFDField
            label="ATK DRONES"
            value={playerState ? playerState.attack_drones : '—'}
          />
        </div>
      </MFDPageBody>
    </>
  );
};

export default React.memo(ThreatPage);
