/**
 * THREAT READINESS — MFD-A page.
 *
 * Environmental readout (sector hazard/radiation/type, own drones) plus
 * personal threat intel: the bounty standing on this pilot, fetched from
 * GET /api/v1/ranking/bounties/target/{id} (player-placed + reputation
 * system bounties). A live bounty raises a CAUTION accent.
 *
 * Field provenance (verified):
 *   currentSector.{hazard_level,radiation_level,type} — Sector interface
 *   playerState.{id,defense_drones,attack_drones}     — PlayerState interface
 *   bounty total_value / player_bounties / system_bounties — BountyService
 */
import React from 'react';
import apiClient from '../../../services/apiClient';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#FF4D6D';

interface BountyStanding {
  total: number;
  hasPlayer: boolean;
  hasSystem: boolean;
}

const ThreatPage: React.FC = () => {
  const { currentSector, playerState, deployMines } = useGame();
  const [bounty, setBounty] = React.useState<BountyStanding | null>(null);
  const [mineQty, setMineQty] = React.useState(1);
  const [mineBusy, setMineBusy] = React.useState(false);
  const [mineMsg, setMineMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const playerId = playerState?.id;
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

  React.useEffect(() => {
    if (!playerId) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await apiClient.get(
          `/api/v1/ranking/bounties/target/${playerId}`,
        );
        if (cancelled) return;
        const player = Array.isArray(data?.player_bounties) ? data.player_bounties : [];
        const system = Array.isArray(data?.system_bounties) ? data.system_bounties : [];
        setBounty({
          total: typeof data?.total_value === 'number' ? data.total_value : 0,
          hasPlayer: player.length > 0,
          hasSystem: system.length > 0,
        });
      } catch {
        // Availability failure stays silent on the panel — the BOUNTY row
        // simply shows "—" rather than throwing.
        if (!cancelled) setBounty(null);
      }
    })();
    return () => { cancelled = true; };
  }, [playerId]);

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

  // Bounty standing: who wants this pilot dead, and for how much.
  let bountyValue: React.ReactNode = '—';
  let bountyHot = false;
  let source: string | null = null;
  if (bounty) {
    if (bounty.total > 0) {
      bountyValue = `${bounty.total.toLocaleString()} cr`;
      bountyHot = true;
      source = bounty.hasPlayer && bounty.hasSystem
        ? 'PLAYER + SYSTEM'
        : bounty.hasPlayer ? 'PLAYER BOARD' : 'SYSTEM';
    } else {
      bountyValue = 'NONE';
    }
  }

  return (
    <>
      <MFDPageHeader title="THREAT READINESS" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="threat-readiness">
        <div className="mfd-page-fields">
          <MFDField label="BOUNTY" value={bountyValue} accent={bountyHot} />
          {source && <MFDField label="WANTED BY" value={source} accent />}
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
