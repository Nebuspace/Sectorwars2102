import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { greyStatusAPI, type GreyStatus } from '../../../services/api';
import { formatCredits } from '../../../utils/formatters';

/**
 * TacticalThreatPage — TACTICAL monitor's THREAT page (WO-UI2-DECK-
 * RECONCILE, §05: "THREAT: law status → CLEAR FINE · mines → LAY 5 ·
 * hazard readout").
 *
 * Relocated from mfd/pages/ThreatPage.tsx (left untouched, read-only
 * source — the mfd-lane's file, not deleted here; a later cleanup WO
 * retires the now-unreachable MFD THRT page per the design brief's own
 * rollup table). Same data sources (currentSector hazard/radiation/type,
 * playerState.mines, GET/POST /combat/grey-status), same hooks/effects —
 * re-laid-out for the compact deck-monitor's screen-hud-content shape
 * instead of MFDPageHeader/MFDPageBody chrome (DeckPageTabs.tsx's own
 * docstring: "mfd.css is a different visual generation" — this monitor
 * doesn't borrow it, same as SOLAR SYSTEM/NAV/TARGET don't).
 */

const GREY_KIND_LABEL: Record<string, string> = {
  player_attack: 'Attacked a lawful player',
  station_attack: 'Attacked a station',
};

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

const TacticalThreatPage: React.FC = () => {
  const { currentSector, playerState, deployMines, updatePlayerCredits } = useGame();
  const [mineQty, setMineQty] = React.useState(1);
  const [mineBusy, setMineBusy] = React.useState(false);
  const [mineMsg, setMineMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const minesCarried = playerState?.mines ?? 0;
  const inOpenSpace = !!playerState && !playerState.is_docked && !playerState.is_landed;

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

  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const greyUntilMs = greyStatus?.greyUntil ? Date.parse(greyStatus.greyUntil) : null;
  const liveRemainingSeconds =
    greyStatus?.isGrey && greyUntilMs !== null ? Math.max(0, Math.round((greyUntilMs - now) / 1000)) : 0;

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
      <div className="empty-state" role="status">
        No sector telemetry
      </div>
    );
  }

  const hazard = currentSector ? currentSector.hazard_level : null;

  return (
    <>
      <div className="threat-section">
        <div className="threat-section-title" role="heading" aria-level={3}>LAW STATUS</div>
        {greyError ? (
          <div className="threat-warnline" role="alert">{greyError}</div>
        ) : greyStatus === null ? (
          <div className="empty-state" role="status">LOADING…</div>
        ) : !greyStatus.isGrey ? (
          <div className="threat-law-clean" role="status">GOOD STANDING — no active grey flag.</div>
        ) : (
          <>
            {/* Frequent (1s-tick) non-urgent update -- aria-live="polite", NOT
                role="alert" (that's reserved for the load-error branch above;
                Pixel a11y gate REVISE, WO-UI2-DECK-RECONCILE). */}
            <div className="threat-warnline" aria-live="polite">
              ⚠ GREY — {GREY_KIND_LABEL[greyStatus.kind ?? ''] || 'Open season'} · clears in{' '}
              {formatCountdown(liveRemainingSeconds)}
            </div>
            {typeof greyStatus.clearFineCredits === 'number' && (
              <div className="threat-row">
                <button
                  type="button"
                  className="threat-btn"
                  onClick={handleClearFine}
                  disabled={greyBusy}
                  aria-busy={greyBusy}
                >
                  {greyBusy ? '…' : `CLEAR FINE ▸ (${formatCredits(greyStatus.clearFineCredits)})`}
                </button>
              </div>
            )}
          </>
        )}
        {greyMsg && <div className={`threat-msg ${greyMsg.ok ? 'ok' : 'err'}`} role="status">{greyMsg.text}</div>}
      </div>

      <div className="threat-section">
        <div className="threat-section-title" role="heading" aria-level={3}>MINES ABOARD {playerState ? minesCarried : '—'}</div>
        {minesCarried < 1 ? (
          <div className="threat-hint">No mines aboard — buy armored mines at a spacedock armory.</div>
        ) : !inOpenSpace ? (
          <div className="threat-hint">Undock / lift off to lay mines in open space.</div>
        ) : (
          <div className="threat-row">
            <input
              type="number"
              min={1}
              max={minesCarried}
              value={mineQty}
              onChange={(e) => setMineQty(Math.max(1, Math.min(minesCarried, parseInt(e.target.value, 10) || 1)))}
              disabled={mineBusy}
              className="threat-mine-input"
              aria-label="Mine quantity"
            />
            <button
              type="button"
              className="threat-btn"
              onClick={handleDeployMines}
              disabled={mineBusy}
              aria-busy={mineBusy}
            >
              {mineBusy ? '…' : `LAY 5 ▸ (carry ${minesCarried})`}
            </button>
          </div>
        )}
        {mineMsg && <div className={`threat-msg ${mineMsg.ok ? 'ok' : 'err'}`} role="status">{mineMsg.text}</div>}
      </div>

      <div className="threat-section">
        <div className="threat-section-title" role="heading" aria-level={3}>HAZARD READOUT</div>
        <div className="system-hazard-metric">
          <div className="hud-label">⚠️ HAZARD</div>
          <div className={`hud-value${(hazard ?? 0) > 0 ? ' danger' : ''}`}>{hazard ?? '—'}/10</div>
          <div className="hud-bar">
            <div className="hud-bar-fill danger" style={{ width: `${(hazard ?? 0) * 10}%` }}></div>
          </div>
        </div>
        <div className="system-hazard-metric">
          <div className="hud-label">☢️ RADIATION</div>
          <div className="hud-value">
            {currentSector ? `${(currentSector.radiation_level * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="system-hazard-metric">
          <div className="hud-label">SECTOR TYPE</div>
          <div className="hud-value">{currentSector?.type ? currentSector.type.toUpperCase() : '—'}</div>
        </div>
      </div>
    </>
  );
};

export default TacticalThreatPage;
