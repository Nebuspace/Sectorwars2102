import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { greyStatusAPI, armoryAPI, type ArmoryMineItem, type GreyStatus, type NavThreatBand } from '../../../services/api';
import { formatCredits } from '../../../utils/formatters';
import LimpetTrackerReadout from '../LimpetTrackerReadout';
import PirateHoldingRaidControl from '../PirateHoldingRaidControl';
import SectorDroneAttackControl from '../SectorDroneAttackControl';
import SectorRetreatControl from '../SectorRetreatControl';
import { useNavThreatRollup } from '../useNavThreatRollup';
import { NAV_THREAT_BAND_CLASS } from '../navThreat';

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Exported for TypeError densify tests (LEG-3146 / LEG-3302). */
export function formatTacticalThreatError(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return fallback;
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && message.trim() && !/^API Error: \d+$/.test(message.trim())) {
    if (isNetworkCollapseMessage(message)) return fallback;
    return message.trim();
  }
  return fallback;
}

/**
 * TacticalThreatPage — TACTICAL monitor's THREAT page (WO-UI2-DECK-
 * RECONCILE, §05: "THREAT: law status → CLEAR FINE · mines → LAY 5 ·
 * hazard readout").
 *
 * Relocated from mfd/pages/ThreatPage.tsx, since DELETED
 * (WO-UI5-RETIREMENT+GLASS — the now-unreachable MFD THRT page, zero
 * remaining consumers). Same data sources (currentSector hazard/radiation/type,
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
  const threatRollup = useNavThreatRollup();
  const [expandedSector, setExpandedSector] = React.useState<number | null>(null);
  const [mineQty, setMineQty] = React.useState(1);
  const [mineItem, setMineItem] = React.useState<ArmoryMineItem>('armored_mine');
  const [limpetCarried, setLimpetCarried] = React.useState(0);
  const [mineBusy, setMineBusy] = React.useState(false);
  const [mineMsg, setMineMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const minesCarried = playerState?.mines ?? 0;
  const carriedForItem = mineItem === 'limpet_mine' ? limpetCarried : minesCarried;
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
      .catch((e: unknown) => {
        setGreyStatus(null);
        setGreyError(formatTacticalThreatError(e, 'Law status unavailable — check your connection.'));
      });
  }, []);

  React.useEffect(() => {
    fetchGreyStatus();
  }, [fetchGreyStatus]);

  React.useEffect(() => {
    armoryAPI
      .getCatalog()
      .then((raw) => {
        const loadout = raw && typeof raw === 'object' ? (raw as { loadout?: { limpet_mines?: number } }).loadout : undefined;
        const n = loadout && typeof loadout.limpet_mines === 'number' ? loadout.limpet_mines : 0;
        setLimpetCarried(n);
      })
      .catch(() => {
        setLimpetCarried(0);
      });
  }, []);

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
    } catch (e: unknown) {
      setGreyMsg({
        ok: false,
        text: formatTacticalThreatError(e, 'Unable to clear fine — check your connection.'),
      });
    } finally {
      setGreyBusy(false);
    }
  };

  const handleDeployMines = async () => {
    if (mineBusy || carriedForItem < 1) return;
    setMineBusy(true);
    setMineMsg(null);
    try {
      const qty = Math.max(1, Math.min(carriedForItem, mineQty));
      const res = await deployMines(qty, mineItem);
      setMineMsg({ ok: true, text: res?.message || `Deployed ${qty} ${mineItem.replace('_', ' ')}(s).` });
      setMineQty(1);
      if (mineItem === 'limpet_mine') {
        setLimpetCarried((n) => Math.max(0, n - qty));
      }
    } catch (e: unknown) {
      setMineMsg({
        ok: false,
        text: formatTacticalThreatError(e, 'Mine deployment failed — check your connection.'),
      });
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
  const threatRows = Object.values(threatRollup.map).sort((a, b) => a.sector_id - b.sector_id);

  const renderBand = (band: NavThreatBand) => (
    <span className={`nav-threat-band ${NAV_THREAT_BAND_CLASS[band]}`}>{band}</span>
  );

  return (
    <>
      <div className="threat-section">
        <div className="threat-section-title" role="heading" aria-level={3}>NAV THREAT ROLLUP</div>
        {threatRollup.loading ? (
          <div className="empty-state" role="status">LOADING…</div>
        ) : threatRollup.error ? (
          <div className="threat-warnline" role="alert">{threatRollup.error}</div>
        ) : threatRows.length === 0 ? (
          <div className="empty-state" role="status">No charted threat data</div>
        ) : (
          <ul className="nav-threat-rollup-list">
            {threatRows.map((row) => (
              <li key={row.sector_id} className="nav-threat-rollup-row">
                <button
                  type="button"
                  className="nav-threat-rollup-toggle"
                  aria-expanded={expandedSector === row.sector_id}
                  onClick={() =>
                    setExpandedSector((cur) => (cur === row.sector_id ? null : row.sector_id))
                  }
                >
                  <span>SECTOR {row.sector_id}</span>
                  {renderBand(row.band)}
                  <span className="nav-threat-score">{row.score}</span>
                </button>
                {expandedSector === row.sector_id && row.contributors?.length > 0 && (
                  <ul className="nav-threat-contributors">
                    {row.contributors.map((c) => (
                      <li key={`${row.sector_id}-${c.input}`}>
                        {c.input}: {c.points}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

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
        <div className="threat-section-title" role="heading" aria-level={3}>
          MINES ABOARD armored {playerState ? minesCarried : '—'} · limpet {limpetCarried}
        </div>
        {minesCarried < 1 && limpetCarried < 1 ? (
          <div className="threat-hint">No mines aboard — buy armored or limpet mines at a spacedock armory.</div>
        ) : !inOpenSpace ? (
          <div className="threat-hint">Undock / lift off to lay mines in open space.</div>
        ) : (
          <div className="threat-row">
            <select
              aria-label="Mine type"
              className="threat-mine-input"
              value={mineItem}
              disabled={mineBusy}
              onChange={(e) => setMineItem(e.target.value as ArmoryMineItem)}
            >
              <option value="armored_mine" disabled={minesCarried < 1}>
                Armored ({minesCarried})
              </option>
              <option value="limpet_mine" disabled={limpetCarried < 1}>
                Limpet ({limpetCarried})
              </option>
            </select>
            <input
              type="number"
              min={1}
              max={Math.max(1, carriedForItem)}
              value={mineQty}
              onChange={(e) => setMineQty(Math.max(1, Math.min(carriedForItem, parseInt(e.target.value, 10) || 1)))}
              disabled={mineBusy || carriedForItem < 1}
              className="threat-mine-input"
              aria-label="Mine quantity"
            />
            <button
              type="button"
              className="threat-btn"
              data-testid="threat-lay-mines"
              onClick={handleDeployMines}
              disabled={mineBusy || carriedForItem < 1}
              aria-busy={mineBusy}
            >
              {mineBusy ? '…' : `LAY ${mineItem === 'limpet_mine' ? 'LIMPET' : 'ARMORED'} ▸`}
            </button>
          </div>
        )}
        {mineMsg && <div className={`threat-msg ${mineMsg.ok ? 'ok' : 'err'}`} role="status">{mineMsg.text}</div>}
      </div>

      <LimpetTrackerReadout />

      <SectorDroneAttackControl />

      <PirateHoldingRaidControl />

      <SectorRetreatControl />

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
