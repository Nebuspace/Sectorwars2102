import React, { useEffect, useState } from 'react';
import { navAPI, type NavThreatBand, type NavThreatEntry } from '../../services/api';
import './tactical-monitor.css';

/**
 * TacticalMonitor — the cockpit's TACTICAL deck-monitor (WO-UI2-TACTICAL-
 * MONITOR). Owns its own screen-hud-header/screen-hud-content (mirrors
 * CommsMailbox's self-contained pattern); GameDashboard's TACTICAL block
 * only supplies the bezel/monitor-screen chrome around it, exactly like
 * the COMMS monitor does around <CommsMailbox/>.
 *
 * Two composed sources, deliberately NOT merged into one number:
 *   1. STATIC known-graph bands — GET /api/v1/nav/threat (fetched here).
 *      Security-ruled STATIC-only: never reflects live remote composition.
 *   2. CURRENT sector LIVE readout — `liveShipCount`, passed down from
 *      GameDashboard's existing `shipsInSector` memo (currentSector.
 *      players_present, self excluded) — zero new backend, zero
 *      duplicated computation.
 *
 * 🔴 A11Y: band chips carry the band NAME as text, never color alone
 * (WCAG). HOSTILE and LETHAL intentionally share the same red — the WO's
 * 3-color status triad, not a 4-color scale — so the word is the only
 * thing distinguishing them, which is exactly the point: color is a
 * reinforcement, never the sole channel.
 */

interface TacticalMonitorProps {
  /** Current sector id, undefined while telemetry hasn't arrived yet. */
  currentSectorId?: number;
  currentSectorName?: string;
  /** Other ships present in the current sector right now (self excluded) —
   *  GameDashboard's existing `shipsInSector.length`. */
  liveShipCount: number;
}

const BAND_LABEL: Record<NavThreatBand, string> = {
  CLEAR: 'CLEAR',
  CAUTION: 'CAUTION',
  HOSTILE: 'HOSTILE',
  LETHAL: 'LETHAL',
};

// STATUS TRIAD only — HOSTILE and LETHAL share the red tier by design
// (WO-UI2-TACTICAL-MONITOR grounding); text carries the HOSTILE/LETHAL
// distinction, not color.
const BAND_CLASS: Record<NavThreatBand, string> = {
  CLEAR: 'tactical-band-clear',
  CAUTION: 'tactical-band-caution',
  HOSTILE: 'tactical-band-danger',
  LETHAL: 'tactical-band-danger',
};

/** A server band this client doesn't recognize fails open to a labeled,
 *  neutral chip rather than crashing or silently rendering blank. */
const bandLabel = (band: string): string => BAND_LABEL[band as NavThreatBand] || band.toUpperCase();
const bandClass = (band: string): string => BAND_CLASS[band as NavThreatBand] || 'tactical-band-unknown';

const BandChip: React.FC<{ band: string; score: number }> = ({ band, score }) => (
  <span className={`tactical-band-chip ${bandClass(band)}`}>
    {bandLabel(band)} <span className="tactical-band-score">{score}</span>
  </span>
);

const TacticalMonitor: React.FC<TacticalMonitorProps> = ({
  currentSectorId,
  currentSectorName,
  liveShipCount,
}) => {
  const [threats, setThreats] = useState<NavThreatEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Fetched on mount and refetched on sector change — mirrors the NAV
  // chart's own fetch effect (GameDashboard.tsx, GET /nav/chart): a move
  // can grow the known graph, and recent_combat contributors decay/renew
  // over time, so the rollup can go stale exactly the way the chart can.
  useEffect(() => {
    let cancelled = false;
    navAPI
      .getThreat()
      .then((data) => {
        if (cancelled) return;
        setThreats(Array.isArray(data) ? data : []);
        setError(null);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(e?.message || 'TACTICAL FEED UNAVAILABLE');
      });
    return () => {
      cancelled = true;
    };
  }, [currentSectorId]);

  const currentThreat =
    threats?.find((t) => t.sector_id === currentSectorId) ?? null;

  // Most-dangerous-first — the operationally useful ordering for a threat
  // overview (sector_id order carries no tactical meaning).
  const sorted = threats ? [...threats].sort((a, b) => b.score - a.score) : [];

  const toggleExpand = (sectorId: number) =>
    setExpandedId((prev) => (prev === sectorId ? null : sectorId));

  return (
    <>
      <div className="screen-hud-header" role="region" aria-label="Tactical">
        <span role="heading" aria-level={2}>TACTICAL</span>
      </div>
      <div className="screen-hud-content tactical-content">
        {/* CURRENT sector — pinned above the scrollable list (Scroll-Law):
            always visible, the "full picture" section combining the
            STATIC band/score with the LIVE ship count. */}
        <div className="tactical-current-section">
          <div className="tactical-current-label" role="heading" aria-level={3}>
            CURRENT SECTOR{currentSectorName ? ` — ${currentSectorName.toUpperCase()}` : ''}
          </div>
          {currentSectorId === undefined ? (
            <div className="empty-state" role="status" aria-live="polite">NO SECTOR TELEMETRY</div>
          ) : (
            <div className="tactical-current-row">
              {/* role=status here (not on the row) so the loaded/error/
                  scanning transition announces without nesting inside the
                  live-readout's own separate role=status below. */}
              <span role="status" aria-live="polite">
                {error ? (
                  <span className="tactical-band-chip tactical-band-unknown">FEED DOWN</span>
                ) : threats === null ? (
                  <span className="tactical-band-chip tactical-band-unknown">SCANNING…</span>
                ) : currentThreat ? (
                  <BandChip band={currentThreat.band} score={currentThreat.score} />
                ) : (
                  <span className="tactical-band-chip tactical-band-unknown">UNKNOWN</span>
                )}
              </span>
              <span
                className={`tactical-live-readout${liveShipCount > 0 ? ' tactical-live-active' : ''}`}
                role="status"
              >
                {liveShipCount > 0
                  ? `⚠ ${liveShipCount} SHIP${liveShipCount === 1 ? '' : 'S'} DETECTED`
                  : 'NO CONTACTS'}
              </span>
            </div>
          )}
        </div>

        {/* Full known-graph list — scrolls WITHIN this region; the
            current-sector section above never scrolls out of view. */}
        <div className="tactical-sector-list" role="list" aria-label="Known sector threat bands">
          {error ? (
            <div className="empty-state" role="status" aria-live="polite">TACTICAL FEED UNAVAILABLE — {error}</div>
          ) : threats === null ? (
            <div className="empty-state" role="status" aria-live="polite">SCANNING KNOWN SECTORS…</div>
          ) : sorted.length === 0 ? (
            <div className="empty-state" role="status" aria-live="polite">NO KNOWN SECTORS CHARTED</div>
          ) : (
            sorted.map((t) => (
              <div key={t.sector_id} className="tactical-sector-row" role="listitem">
                <button
                  type="button"
                  className="tactical-sector-summary"
                  onClick={() => toggleExpand(t.sector_id)}
                  aria-expanded={expandedId === t.sector_id}
                  aria-controls={`tactical-contributors-${t.sector_id}`}
                >
                  <span className="tactical-sector-id">
                    SECTOR {t.sector_id}
                    {t.sector_id === currentSectorId ? ' (CURRENT)' : ''}
                  </span>
                  <BandChip band={t.band} score={t.score} />
                </button>
                {expandedId === t.sector_id && (
                  <div className="tactical-contributors" id={`tactical-contributors-${t.sector_id}`}>
                    {t.contributors.length === 0 ? (
                      <span className="tactical-contributors-empty">No contributing factors</span>
                    ) : (
                      t.contributors.map((c, i) => (
                        <span key={c.input} className="tactical-contributor">
                          {i > 0 && ' · '}
                          {c.input} {c.points}
                        </span>
                      ))
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
};

export default TacticalMonitor;
