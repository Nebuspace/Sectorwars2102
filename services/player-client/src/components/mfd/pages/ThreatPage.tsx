/**
 * THREAT READINESS — MFD-A page (slimmed, WO-PLAYERINFO id=146).
 *
 * Sector-threat readout (hazard / radiation / type) + the minefield deploy
 * action. The BOUNTY standing and OWN DRONES moved to the always-on HUD
 * (id=145: bounty chip + ATK/DEF DRONES), so this page is now sector threat
 * intel + minefield only — no per-pilot bounty fetch, no drone duplication.
 *
 * Field provenance (verified):
 *   currentSector.{hazard_level,radiation_level,type} — Sector interface
 *   playerState.{mines, is_docked, is_landed}         — PlayerState interface
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#FF4D6D';

const ThreatPage: React.FC = () => {
  const { currentSector, playerState, deployMines } = useGame();
  const [mineQty, setMineQty] = React.useState(1);
  const [mineBusy, setMineBusy] = React.useState(false);
  const [mineMsg, setMineMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const minesCarried = playerState?.mines ?? 0;
  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;

  const handleDeployMines = async () => {
    if (mineBusy || minesCarried < 1) return;
    setMineBusy(true);
    setMineMsg(null);
    try {
      const qty = Math.max(1, Math.min(minesCarried, mineQty));
      const res = await deployMines(qty);
      setMineMsg({ ok: true, text: res?.message || `Deployed ${qty} mine(s).` });
      setMineQty(1);
    } catch (e: any) {
      setMineMsg({ ok: false, text: e?.response?.data?.detail || 'Mine deployment failed' });
    } finally {
      setMineBusy(false);
    }
  };

  if (!currentSector && !playerState) {
    return (
      <>
        <MFDPageHeader title="THREAT READINESS" accent={ACCENT} status="shipped" />
        <MFDPageBody scrollKey="threat-readiness">
          <MFDInsufficient />
        </MFDPageBody>
      </>
    );
  }

  const hazard = currentSector ? currentSector.hazard_level : null;

  return (
    <>
      <MFDPageHeader title="THREAT READINESS" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="threat-readiness">
        <div className="mfd-page-fields">
          <MFDField label="HAZARD LVL" value={hazard ?? '—'} accent={(hazard ?? 0) > 0} />
          <MFDField
            label="RADIATION"
            value={currentSector ? currentSector.radiation_level : '—'}
          />
          <MFDField
            label="SECTOR TYPE"
            value={currentSector?.type ? currentSector.type.toUpperCase() : '—'}
          />
          <MFDField label="MINES" value={playerState ? minesCarried : '—'} accent={minesCarried > 0} />
        </div>

        {/* Lay armored mines in the current sector (open space only). */}
        <div className="mfd-mine-deploy">
          <div className="mfd-mine-head">⚓ LAY MINEFIELD</div>
          {minesCarried < 1 ? (
            <div className="mfd-mine-hint">No mines aboard — buy armored mines at a spacedock armory.</div>
          ) : !inOpenSpace ? (
            <div className="mfd-mine-hint">Undock / lift off to lay mines in open space.</div>
          ) : (
            <div className="mfd-mine-row">
              <input
                type="number"
                min={1}
                max={minesCarried}
                value={mineQty}
                onChange={(e) => setMineQty(Math.max(1, Math.min(minesCarried, parseInt(e.target.value) || 1)))}
                disabled={mineBusy}
                className="mfd-mine-input"
              />
              <button className="mfd-mine-btn" onClick={handleDeployMines} disabled={mineBusy}>
                {mineBusy ? '…' : `Deploy (carry ${minesCarried})`}
              </button>
            </div>
          )}
          {mineMsg && (
            <div className={`mfd-mine-msg ${mineMsg.ok ? 'ok' : 'err'}`}>{mineMsg.text}</div>
          )}
        </div>
      </MFDPageBody>
    </>
  );
};

export default React.memo(ThreatPage);
