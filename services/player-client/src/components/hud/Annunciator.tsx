import React, { useRef } from 'react';
import { useAnnunciatorState, segLitClass, type MasterBulb, type Segment } from './useAnnunciatorState';
import HazardAnalysisCard from './HazardAnalysisCard';
import '../layouts/cockpit-shell.css';
import './annunciator.css';

/**
 * Annunciator — the windshield HUD overlay, rendered as the canonical SLIM
 * LAMP STRIP (WO-UI1-CHROME-COMPLETE, closing out WO-UI1-ANNUNCIATOR):
 *
 *   [WARN] · HAZARD  LAW  THREAT  TURNS  COMM · [CAUT]
 *
 * — one slim horizontal strip, reproduced from the ratified prototype's own
 * `.annun` markup (audit/design-briefs/cockpit-redesign-v10-RATIFIED.html
 * §05 L513-515/L631, :1203-1213) rather than the earlier bordered-box form.
 * IDLE lamps render as barely-visible dark chips; LIT lamps colorize by
 * severity. All 7 buttons (2 master bulbs + 5 segments) are ALWAYS mounted
 * — unlike the prior sub-part, which only rendered a chip when active — so
 * the strip reads as a persistent instrument, not a conditional toast rail.
 *
 * WO-UI0-SHELL-TRANSPLANT (leaf L5): the DOM now emits the artifact's own
 * BARE classnames (`.annun`/`.lamp`/`.bulb`/`.segs`/`.seg`) instead of the
 * prior sub-part's `.annunciator-*` prefixed set — cockpit-shell.css (the
 * transplant's untouched CSS baseline, imported alongside this file's own
 * annunciator.css) now owns the strip's geometry AND severity/state colors
 * (`.bulb.warn.on`, `.seg.live` etc) 1:1 from the ratified prototype;
 * annunciator.css keeps only what cockpit-shell.css doesn't cover — the
 * `.annunciator-overlay` scene-narrowing wrapper (below) and the
 * HazardAnalysisCard dialog skin. The outer `.annunciator-overlay` wrapper
 * (NOT a bare artifact class — this app's own scene-narrowing/z-index
 * contract, no demo equivalent) stays; `.annun` self-positions inside it via
 * cockpit-shell.css's own `position:absolute;top:5%;left:50%;
 * transform:translateX(-50%)`, so the wrapper no longer needs its own
 * flex-centering for the strip (kept only for the hazard card, which gets
 * its own absolute placement below the strip in annunciator.css).
 *
 * Two ack-able MASTER bulbs (WARN red/fast, CAUTION amber/slow) flank five
 * click-through SEGMENTS (HAZARD·LAW·THREAT·TURNS·COMM) that are pure state
 * indicators + navigators — only the bulbs silence a flash; segments always
 * reflect live state (mirrors the prototype: only `.bulb` has an ack
 * onclick, `.seg` buttons navigate). COMM is info-class (cyan), never
 * feeding either master bulb — "never sharing the danger lane" (canon).
 *
 * All trigger/lifecycle/navigation logic lives in useAnnunciatorState.ts
 * (shared with AnnunciatorMini.tsx) — see that file's doc-comment for the
 * full state-source table, the LAW CSS-class-vs-boolean doc-gap (now
 * resolved for the CLASS side by NIT n5, `segLitClass()`; the underlying
 * canon question is still staged for Max), and the click-through mechanism
 * per lamp.
 *
 * Overlay contract (SCENE-NARROWING guardrail), unchanged from the prior
 * sub-part: the wrapper's pointer-events:none is set INLINE (testable in
 * this project's node/jsdom vitest environment, which does not process CSS
 * imports) and repeated in annunciator.css; position:absolute/inset:0 so it
 * contributes zero layout size to its parent (GameLayout's `.band` —
 * WO-UI0-SHELL-TRANSPLANT, supersedes the retired `.windshield-hud-anchor`);
 * only interactive chrome (lamp buttons, the hazard card) opts back into
 * pointer-events.
 */

const MASTER_LABEL: Record<MasterBulb['id'], string> = { WARN: 'WARN', CAUT: 'CAUT' };

/* Pixel a11y fix-pass (WCAG 4.1.3-adjacent SR-transition defect): role and
 * aria-live used to be added/removed on the button as a lamp lit/idled --
 * some screen readers only pick up a live region's content changes if the
 * region (and its role) was ALREADY present in the accessibility tree
 * before the change, so toggling role/aria-live in step with `active`
 * could silently eat the very transition the live region exists to
 * announce. Fixed by making both STATIC per severity (present on every
 * render, active or idle) -- only the aria-label CONTENT changes to convey
 * state (describeMaster/describeSegment in useAnnunciatorState.ts already
 * built full state text either way, so this is a pure attribute-stability
 * fix, not a new state-description). Accepted trade-off: an idle WARN-
 * severity element (the WARN bulb, the THREAT segment) permanently carries
 * role="alert" -- most screen readers only announce role=alert content on
 * a CHANGE, not merely being present/mounted, so this does not spam idle
 * mount; if a specific AT is found to over-announce, revisit per-lamp. */
const roleFor = (severity: 'warn' | 'caution' | 'info' | 'WARN' | 'CAUT'): 'alert' | 'status' =>
  severity === 'warn' || severity === 'WARN' ? 'alert' : 'status';
const ariaLiveFor = (severity: 'warn' | 'caution' | 'info' | 'WARN' | 'CAUT'): 'assertive' | 'polite' =>
  severity === 'warn' || severity === 'WARN' ? 'assertive' : 'polite';

/* Bare artifact markup (RATIFIED.html:1204/1212): a blank `.bulb` button —
 * no icon, no text INSIDE it — followed by its WARN/CAUT label as a plain
 * text sibling inside `.lamp` (cockpit-shell.css styles that text via
 * `.lamp`'s own inherited font-size/color/letter-spacing, no dedicated
 * label class needed). */
const MasterBulbButton: React.FC<{ bulb: MasterBulb; reducedMotion: boolean }> = ({ bulb, reducedMotion }) => {
  const severityClass = bulb.id === 'WARN' ? 'warn' : 'caut';
  const stateClass = bulb.active ? (bulb.flashing && !reducedMotion ? 'on' : 'ack') : '';
  return (
    <div className="lamp">
      <button
        type="button"
        className={['bulb', severityClass, stateClass].filter(Boolean).join(' ')}
        onClick={bulb.ack}
        aria-label={bulb.ariaLabel}
        role={roleFor(bulb.id)}
        aria-live={ariaLiveFor(bulb.id)}
        style={{ pointerEvents: 'auto' }}
      />
      {MASTER_LABEL[bulb.id]}
    </div>
  );
};

const SegmentButton = React.forwardRef<HTMLButtonElement, { segment: Segment }>(({ segment }, ref) => (
  <button
    ref={ref}
    type="button"
    className={['seg', segment.active ? segLitClass(segment) : ''].filter(Boolean).join(' ')}
    onClick={segment.onActivate}
    aria-label={segment.ariaLabel}
    title={segment.title}
    role={roleFor(segment.severity)}
    aria-live={ariaLiveFor(segment.severity)}
    style={{ pointerEvents: 'auto' }}
  >
    {segment.id}
  </button>
));
SegmentButton.displayName = 'SegmentButton';

const Annunciator: React.FC = () => {
  const { warn, caution, segments, reducedMotion, hazardCardOpen, closeHazardCard, currentSector } =
    useAnnunciatorState();

  // Pixel a11y fix-pass (WCAG 2.4.3): focus must RETURN to the HAZARD lamp
  // that opened the card when it closes (Escape or the close button --
  // HazardAnalysisCard.tsx handles moving focus IN on open; this half
  // moves it back OUT on close). The ref is attached only to the HAZARD
  // segment button below.
  const hazardButtonRef = useRef<HTMLButtonElement>(null);
  const handleCloseHazardCard = () => {
    closeHazardCard();
    hazardButtonRef.current?.focus();
  };

  return (
    <div className="annunciator-overlay" style={{ pointerEvents: 'none' }} data-testid="annunciator-overlay">
      <div className="annun" data-testid="annunciator-strip">
        <MasterBulbButton bulb={warn} reducedMotion={reducedMotion} />
        <div className="segs">
          {segments.map((segment) => (
            <SegmentButton key={segment.id} segment={segment} ref={segment.id === 'HAZARD' ? hazardButtonRef : undefined} />
          ))}
        </div>
        <MasterBulbButton bulb={caution} reducedMotion={reducedMotion} />
      </div>
      {hazardCardOpen && <HazardAnalysisCard sector={currentSector} onClose={handleCloseHazardCard} />}
    </div>
  );
};

export default Annunciator;
