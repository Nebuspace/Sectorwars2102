/**
 * VESSEL STATUS — MFD-A page (NEON15, zone B2).
 *
 * Field provenance (verified against live types/usages):
 *   currentShip.name/.type            — Ship interface (GameContext.tsx)
 *   currentShip.combat.{hull,max_hull,shields,max_shields}
 *                                     — loose `combat` dict; same defensive
 *                                       number narrowing SpaceDockInterface uses
 *   currentShip.maintenance.{current_rating,failure_status}
 *                                     — loose dict, mirrors ShipSelector reads
 *   playerState.{defense_drones,attack_drones} — PlayerState interface
 * Absent values render '—' per the contract honesty rule — never a fake 0.
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty } from '../atoms';
import './pages-ship.css';

const ACCENT = '#00D9FF';

const asRecord = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

/** "412 / 500" when both legs exist, lone value when only current does, else em dash. */
const gauge = (current: number | null, max: number | null): string => {
  if (current !== null && max !== null) return `${current} / ${max}`;
  if (current !== null) return `${current}`;
  return '—';
};

const VesselPage: React.FC = () => {
  const { currentShip, playerState } = useGame();

  if (!currentShip) {
    return (
      <>
        <MFDPageHeader title="VESSEL STATUS" accent={ACCENT} status="shipped" />
        <MFDPageBody scrollKey="vessel-status">
          <MFDEmpty text="NO ACTIVE VESSEL" />
        </MFDPageBody>
      </>
    );
  }

  const combat = asRecord(currentShip.combat as unknown);
  const maintenance = asRecord(currentShip.maintenance as unknown);
  const conditionRating = num(maintenance?.['current_rating']);
  const failureStatus = maintenance?.['failure_status'];
  const failureText =
    typeof failureStatus === 'string' && failureStatus !== '' && failureStatus !== 'NONE'
      ? failureStatus
      : null;

  return (
    <>
      <MFDPageHeader title="VESSEL STATUS" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="vessel-status">
        <div className="mfd-page-fields">
          <MFDField label="VESSEL" value={currentShip.name || '—'} accent />
          <MFDField
            label="CLASS"
            value={currentShip.type ? currentShip.type.replace(/_/g, ' ') : '—'}
          />
          <MFDField
            label="HULL"
            value={gauge(num(combat?.['hull']), num(combat?.['max_hull']))}
          />
          <MFDField
            label="SHIELDS"
            value={gauge(num(combat?.['shields']), num(combat?.['max_shields']))}
          />
          <MFDField
            label="CONDITION"
            value={conditionRating !== null ? `${conditionRating}%` : '—'}
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
        {failureText && (
          <div className="mfd-page-warnline" role="alert">
            {failureText} FAILURE DETECTED
          </div>
        )}
      </MFDPageBody>
    </>
  );
};

export default React.memo(VesselPage);
