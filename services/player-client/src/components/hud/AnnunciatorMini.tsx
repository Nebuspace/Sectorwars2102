import React from 'react';
import { useAnnunciatorState, segLitClass, type MasterBulb, type Segment } from './useAnnunciatorState';
import HazardAnalysisCard from './HazardAnalysisCard';
import '../layouts/cockpit-shell.css';
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
 * />` drop-in). GameLayout's `.band` mount (WO-UI0-SHELL-TRANSPLANT —
 * supersedes the retired `.windshield-hud-anchor`) already keeps the FULL
 * strip visible across all three modes (mode classes now resize `.band`'s
 * own height directly — cockpit-shell.css + game-layout.css's
 * `.mode-station .band`/`.mode-surface .band` — the band itself persists
 * across all three, just at a different fixed height per mode), so there
 * is no current gap this needs to fill — it is built ready, not
 * force-mounted into an unrelated file (e.g. TacticalMonitor.tsx's own
 * flight-deck header) where it would just duplicate the always-visible
 * strip for no canon-specified reason. Wiring it into a future station/
 * surface header is a one-line import once that header exists.
 *
 * WO-UI0-SHELL-TRANSPLANT (leaf L5): bulbs/segments carry the BARE artifact
 * classes (`.bulb`/`.seg`) ALONGSIDE their existing `.annunciator-mini-*`
 * companions — NOT a straight swap like the full strip's Annunciator.tsx.
 * The artifact has no "mini" variant of its own to map onto, and `.bulb`/
 * `.seg`'s cockpit-shell.css geometry is baked to the FULL strip's size
 * (3.4em/1.7em bulbs) — applying it bare would defeat "compact monitor-
 * header variant" entirely. So this file keeps `.annunciator-mini-bulb`/
 * `.annunciator-mini-seg` as SIZE-only overrides (font-size/dimensions/
 * padding, no color) written as `.bulb.annunciator-mini-bulb`/
 * `.seg.annunciator-mini-seg` compound selectors in annunciator.css — that
 * 2-class specificity beats the bare single-class cockpit-shell.css rule
 * regardless of stylesheet import order (no fragile load-order bet), while
 * every color/state rule (`.bulb.warn.on`, `.seg.live`, `.seg.livec`,
 * `.seg.livecm`, the ack states) still comes from cockpit-shell.css for
 * free via the shared bare class — zero duplicated color CSS. `.annun`
 * itself (position:absolute;top:5%;left:50%;transform — an OVERLAY
 * contract) is deliberately NOT reused here at all: this component is an
 * inline row meant to sit inside a header's own flex layout, the exact
 * opposite contract, so `.annunciator-mini` (unchanged, not a bare target —
 * no artifact equivalent exists) stays the root class. `segLitClass()`
 * (useAnnunciatorState.ts) is shared with Annunciator.tsx so LAW's NIT n5
 * override (WARN-red `.live`, not its own caution `.livec`) can't drift
 * between the two views.
 */

const MASTER_LABEL: Record<MasterBulb['id'], string> = { WARN: 'W', CAUT: 'C' };

const MiniBulb: React.FC<{ bulb: MasterBulb; reducedMotion: boolean }> = ({ bulb, reducedMotion }) => {
  const severityClass = bulb.id === 'WARN' ? 'warn' : 'caut';
  const stateClass = bulb.active ? (bulb.flashing && !reducedMotion ? 'on' : 'ack') : '';
  return (
    <button
      type="button"
      className={['bulb', 'annunciator-mini-bulb', severityClass, stateClass].filter(Boolean).join(' ')}
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
    className={['seg', 'annunciator-mini-seg', segment.active ? segLitClass(segment) : ''].filter(Boolean).join(' ')}
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
      <div className="segs annunciator-mini-segs">
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
