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
  // The server stores the maintenance rating as `condition` (0-100); keep
  // `current_rating` as a fallback for older payloads.
  const conditionRating = num(maintenance?.['condition']) ?? num(maintenance?.['current_rating']);
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
        {/* GENESIS BAY on STATUS (id=143): mirror CRGO's slot-grid + count so a
            ship's genesis devices are visible on STAT too. Read-only here — the
            deploy affordance stays prominent on CRGO (CargoPage). */}
        {(() => {
          const maxGenesis = num(currentShip.max_genesis_devices) ?? 0;
          const loadedGenesis = num(currentShip.genesis_devices) ?? 0;
          if (maxGenesis <= 0) return null;
          return (
            <div className="mfd-page-section">
              <div className="mfd-page-section-title">GENESIS BAY</div>
              <div className="mfd-page-genesis-row">
                <div className="mfd-page-genesis-slots">
                  {Array.from({ length: maxGenesis }, (_, i) => {
                    const loaded = i < loadedGenesis;
                    return (
                      <span
                        key={i}
                        className={`mfd-page-genesis-slot ${loaded ? 'loaded' : 'empty'}`}
                        title={loaded ? 'Genesis Device Loaded' : 'Empty Slot'}
                      >
                        {loaded ? '◉' : '○'}
                      </span>
                    );
                  })}
                </div>
                <span className={`mfd-page-genesis-count${loadedGenesis > 0 ? ' active' : ''}`}>
                  {loadedGenesis} / {maxGenesis}
                </span>
              </div>
            </div>
          );
        })()}
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
