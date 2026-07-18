import React from 'react';
import { useAnnunciatorState, segLitClass, roleFor, ariaLiveFor, type MasterBulb, type Segment } from './useAnnunciatorState';
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
 * WO-HUD-LIGHTS phase 1: still NOT mounted anywhere (unchanged — see the
 * paragraph below), but updated in lockstep with useAnnunciatorState.ts's
 * ALERT-master/4-segment rewire anyway: this file destructures that hook's
 * return shape directly (`warn`/`caution` → `alert`, `TURNS` dropped from
 * the segment list), so leaving it untouched would break `tsc`, not just
 * drift silently. Changes here are mechanical type-following only — no new
 * behavior, no expanded scope, still built-ready/unmounted.
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
 * (useAnnunciatorState.ts) is shared with Annunciator.tsx so neither view
 * can drift on which class a given segment lights up with (WO-HUD-LIGHTS
 * phase 1: this function is now a straight severity→class lookup — the
 * prior LAW-specific "render WARN-red regardless of severity" special-case
 * was retired along with the trigger it existed to match, see
 * useAnnunciatorState.ts's own doc-comment).
 */

const MASTER_LABEL: Record<MasterBulb['id'], string> = { ALERT: 'A' };

// Severity class hardcoded to `warn` — see Annunciator.tsx's own
// MasterBulbButton doc-comment: the single consolidated master reuses the
// old WARN bulb's CSS profile rather than a new `.bulb.alert` rule.
// role/aria-live are STATIC (roleFor/ariaLiveFor, shared with
// Annunciator.tsx) — WO-HUD-LIGHTS Pixel REVISE: this file previously
// toggled them on/off with `active`, a known SR-transition bug the mounted
// strip was already fixed for (see useAnnunciatorState.ts's own
// doc-comment on the two helpers).
const MiniBulb: React.FC<{ bulb: MasterBulb; reducedMotion: boolean }> = ({ bulb, reducedMotion }) => {
  const stateClass = bulb.active ? (bulb.flashing && !reducedMotion ? 'on' : 'ack') : '';
  return (
    <button
      type="button"
      className={['bulb', 'annunciator-mini-bulb', 'warn', stateClass].filter(Boolean).join(' ')}
      onClick={bulb.ack}
      aria-label={bulb.ariaLabel}
      role={roleFor(bulb.id)}
      aria-live={ariaLiveFor(bulb.id)}
    >
      {MASTER_LABEL[bulb.id]}
    </button>
  );
};

// 3-letter abbreviations, distinct — kept as real prefixes of the full
// label rather than arbitrary glyphs, so the mini strip's visible text
// still traces back to the canon segment name (HAZARD·LAW·THREAT·COMM).
const MINI_SEGMENT_LABEL: Record<Segment['id'], string> = {
  HAZARD: 'HAZ',
  LAW: 'LAW',
  THREAT: 'THR',
  COMM: 'COM',
};

const MiniSegment: React.FC<{ segment: Segment }> = ({ segment }) => (
  <button
    type="button"
    className={['seg', 'annunciator-mini-seg', segment.active ? segLitClass(segment) : ''].filter(Boolean).join(' ')}
    onClick={segment.onActivate}
    aria-label={segment.ariaLabel}
    title={segment.title}
    role={roleFor(segment.severity)}
    aria-live={ariaLiveFor(segment.severity)}
  >
    {MINI_SEGMENT_LABEL[segment.id]}
  </button>
);

const AnnunciatorMini: React.FC = () => {
  const { alert, segments, reducedMotion, hazardCardOpen, closeHazardCard, currentSector } =
    useAnnunciatorState();

  return (
    <div className="annunciator-mini" data-testid="annunciator-mini">
      <MiniBulb bulb={alert} reducedMotion={reducedMotion} />
      <div className="segs annunciator-mini-segs">
        {segments.map((segment) => (
          <MiniSegment key={segment.id} segment={segment} />
        ))}
      </div>
      {hazardCardOpen && <HazardAnalysisCard sector={currentSector} onClose={closeHazardCard} />}
    </div>
  );
};

export default AnnunciatorMini;
