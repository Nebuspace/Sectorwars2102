import React from 'react';
import { useAnnunciatorState, type MasterBulb, type Segment } from './useAnnunciatorState';
import HazardAnalysisCard from './HazardAnalysisCard';
import './annunciator.css';

/**
 * AnnunciatorMini — the compact monitor-header annunciator variant
 * (WO-UI1-CHROME-COMPLETE item 7: "a mini annunciator variant for the
 * monitor headers"; canon SCOPE lines for WO-UI3-STATION-MODE/WO-UI3-
 * SURFACE-MODE: "header w/ mini-annunciator"). Reads the SAME
 * useAnnunciatorState() hook as the full windshield strip (Annunciator.tsx)
 * — identical triggers, ack/click-through semantics, single source of
 * truth — just a denser inline row suited to a header's tight height,
 * with NO absolute-overlay positioning (unlike the windshield strip, this
 * is meant to sit inline inside a header flex row, not float over a scene).
 *
 * NOT YET MOUNTED anywhere (flagged, not silently orphaned): its intended
 * consumers per canon are the STATION bay-band header and the SURFACE
 * panorama header — both owned by WO-UI3-STATION-MODE / WO-UI3-SURFACE-
 * MODE, neither built yet in this tree (no DockingSequence.tsx or surface
 * panorama header component exists to receive a one-line `<AnnunciatorMini
 * />` drop-in). GameLayout's existing `.windshield-hud-anchor` mount
 * already keeps the FULL strip visible across all three modes (mode
 * classes only resize --band-h, the windshield grid area itself persists),
 * so there is no current gap this needs to fill — it is built ready, not
 * force-mounted into an unrelated file (e.g. TacticalMonitor.tsx's own
 * flight-deck header) where it would just duplicate the always-visible
 * strip for no canon-specified reason. Wiring it into a future station/
 * surface header is a one-line import once that header exists.
 */

const MASTER_LABEL: Record<MasterBulb['id'], string> = { WARN: 'W', CAUT: 'C' };

const MiniBulb: React.FC<{ bulb: MasterBulb; reducedMotion: boolean }> = ({ bulb, reducedMotion }) => {
  const severityClass = bulb.id === 'WARN' ? 'warn' : 'caut';
  const stateClass = bulb.active ? (bulb.flashing && !reducedMotion ? 'on' : 'ack') : '';
  return (
    <button
      type="button"
      className={['annunciator-mini-bulb', severityClass, stateClass].filter(Boolean).join(' ')}
      onClick={bulb.ack}
      aria-label={bulb.ariaLabel}
      role={bulb.active ? (bulb.id === 'WARN' ? 'alert' : 'status') : undefined}
      aria-live={bulb.active ? (bulb.id === 'WARN' ? 'assertive' : 'polite') : undefined}
    >
      {MASTER_LABEL[bulb.id]}
    </button>
  );
};

// 3-letter abbreviations, distinct (segment.id[0] alone collides: THREAT and
// TURNS both start with T) — kept as real prefixes of the full label rather
// than arbitrary glyphs, so the mini strip's visible text still traces back
// to the canon segment name (HAZARD·LAW·THREAT·TURNS·COMM).
const MINI_SEGMENT_LABEL: Record<Segment['id'], string> = {
  HAZARD: 'HAZ',
  LAW: 'LAW',
  THREAT: 'THR',
  TURNS: 'TRN',
  COMM: 'COM',
};

const MiniSegment: React.FC<{ segment: Segment }> = ({ segment }) => (
  <button
    type="button"
    className={['annunciator-mini-seg', `annunciator-mini-seg--${segment.severity}`, segment.active ? 'is-live' : ''].filter(Boolean).join(' ')}
    onClick={segment.onActivate}
    aria-label={segment.ariaLabel}
    title={segment.title}
    role={segment.active ? (segment.severity === 'warn' ? 'alert' : 'status') : undefined}
    aria-live={segment.active ? (segment.severity === 'warn' ? 'assertive' : 'polite') : undefined}
  >
    {MINI_SEGMENT_LABEL[segment.id]}
  </button>
);

const AnnunciatorMini: React.FC = () => {
  const { warn, caution, segments, reducedMotion, hazardCardOpen, closeHazardCard, currentSector } =
    useAnnunciatorState();

  return (
    <div className="annunciator-mini" data-testid="annunciator-mini">
      <MiniBulb bulb={warn} reducedMotion={reducedMotion} />
      <div className="annunciator-mini-segs">
        {segments.map((segment) => (
          <MiniSegment key={segment.id} segment={segment} />
        ))}
      </div>
      <MiniBulb bulb={caution} reducedMotion={reducedMotion} />
      {hazardCardOpen && <HazardAnalysisCard sector={currentSector} onClose={closeHazardCard} />}
    </div>
  );
};

export default AnnunciatorMini;
