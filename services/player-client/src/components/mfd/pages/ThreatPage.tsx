/**
 * THREAT READINESS — MFD-A page (slimmed, WO-PLAYERINFO id=146).
 *
 * Sector-threat readout (hazard / radiation / type) + the minefield deploy
 * action, plus the player's own LAW STATUS (WO-UIPC-LAW-GREY-STATUS): the
 * shipped grey-flag suspect lifecycle (GET/POST /combat/grey-status) had
 * zero client surface before this — a player could be marked "open season"
 * with no way to see it or pay the fine to clear early. The BOUNTY standing
 * and OWN DRONES moved to the always-on HUD (id=145: bounty chip + ATK/DEF
 * DRONES), so sector-threat + minefield + law status is the full page.
 *
 * Field provenance (verified):
 *   currentSector.{hazard_level,radiation_level,type} — Sector interface
 *   playerState.{mines, is_docked, is_landed}         — PlayerState interface
 *   GreyStatus (GET /api/v1/combat/grey-status)       — api.ts GreyStatus
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { greyStatusAPI, type GreyStatus } from '../../../services/api';
import { formatCredits } from '../../../utils/formatters';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#FF4D6D';

const GREY_KIND_LABEL: Record<string, string> = {
  player_attack: 'Attacked a lawful player',
  station_attack: 'Attacked a station',
};

// Mirrors TurnEconomyPage's TIME-TO-FULL formatter -- same 1s-ticker idiom.
const formatCountdown = (totalSeconds: number): string => {
  const s = Math.max(0, Math.ceil(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (h > 0) return `${h}h ${pad(m)}m ${pad(sec)}s`;
  if (m > 0) return `${m}m ${pad(sec)}s`;
  return `${sec}s`;
};

const ThreatPage: React.FC = () => {
  const { currentSector, playerState, deployMines, updatePlayerCredits } = useGame();
  const [mineQty, setMineQty] = React.useState(1);
  const [mineBusy, setMineBusy] = React.useState(false);
  const [mineMsg, setMineMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const minesCarried = playerState?.mines ?? 0;
  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;

  // LAW STATUS (grey-flag) — independent fetch, own loading/error/action state.
  const [greyStatus, setGreyStatus] = React.useState<GreyStatus | null>(null);
  const [greyError, setGreyError] = React.useState<string | null>(null);
  const [greyBusy, setGreyBusy] = React.useState(false);
  const [greyMsg, setGreyMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const fetchGreyStatus = React.useCallback(() => {
    greyStatusAPI
      .getStatus()
      .then((status) => {
        setGreyStatus(status);
        setGreyError(null);
      })
      .catch((e: any) => {
        setGreyStatus(null);
        setGreyError(e?.message || 'Failed to load law status');
      });
  }, []);

  React.useEffect(() => {
    fetchGreyStatus();
  }, [fetchGreyStatus]);

  // Live 1s ticker for the countdown (mirrors TurnEconomyPage's TIME TO
  // FULL projection) — greyUntil is authoritative, remainingSeconds is only
  // the value as of the last fetch.
  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const greyUntilMs = greyStatus?.greyUntil ? Date.parse(greyStatus.greyUntil) : null;
  const liveRemainingSeconds =
    greyStatus?.isGrey && greyUntilMs !== null ? Math.max(0, Math.round((greyUntilMs - now) / 1000)) : 0;

  // The local countdown hitting zero means the flag has lapsed server-side
  // too (greyUntil is authoritative) -- refetch once to drop into the clean
  // state instead of showing a stale "GREY, 0s" forever.
  const expiredRef = React.useRef(false);
  React.useEffect(() => {
    if (greyStatus?.isGrey && liveRemainingSeconds === 0) {
      if (!expiredRef.current) {
        expiredRef.current = true;
        fetchGreyStatus();
      }
    } else {
      expiredRef.current = false;
    }
  }, [greyStatus, liveRemainingSeconds, fetchGreyStatus]);

  const handleClearFine = async () => {
    if (greyBusy || !greyStatus?.isGrey || typeof greyStatus.clearFineCredits !== 'number') return;
    setGreyBusy(true);
    setGreyMsg(null);
    try {
      const result = await greyStatusAPI.clearFine();
      if (result.success) {
        if (typeof result.creditsRemaining === 'number') {
          updatePlayerCredits(result.creditsRemaining);
        }
        setGreyMsg({ ok: true, text: result.message || `Fine paid — ${formatCredits(result.finePaid)}.` });
        fetchGreyStatus();
      } else {
        setGreyMsg({ ok: false, text: result.message || 'Unable to clear fine' });
      }
    } catch (e: any) {
      setGreyMsg({ ok: false, text: e?.message || 'Failed to clear fine' });
    } finally {
      setGreyBusy(false);
    }
  };

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

        {/* Grey-flag suspect lifecycle (WO-UIPC-LAW-GREY-STATUS) — shipped
            backend, previously invisible to the player. */}
        <div className="mfd-page-section">
          <div className="mfd-page-section-title">LAW STATUS</div>
          {greyError ? (
            <div className="mfd-page-warnline">{greyError}</div>
          ) : greyStatus === null ? (
            <MFDEmpty text="LOADING…" />
          ) : !greyStatus.isGrey ? (
            <div className="mfd-law-clean">GOOD STANDING — no active grey flag.</div>
          ) : (
            <>
              <div className="mfd-page-warnline">
                ⚠ GREY — {GREY_KIND_LABEL[greyStatus.kind ?? ''] || 'Open season'} · clears in{' '}
                {formatCountdown(liveRemainingSeconds)}
              </div>
              {typeof greyStatus.clearFineCredits === 'number' && (
                <div className="mfd-law-row">
                  <button
                    type="button"
                    className="mfd-law-btn"
                    onClick={handleClearFine}
                    disabled={greyBusy}
                  >
                    {greyBusy ? '…' : `Clear Fine (${formatCredits(greyStatus.clearFineCredits)})`}
                  </button>
                </div>
              )}
            </>
          )}
          {greyMsg && (
            <div className={`mfd-law-msg ${greyMsg.ok ? 'ok' : 'err'}`}>{greyMsg.text}</div>
          )}
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
