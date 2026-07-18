import React, { useRef } from 'react';
import { useAnnunciatorState, segLitClass, roleFor, ariaLiveFor, type MasterBulb, type Segment } from './useAnnunciatorState';
import HazardAnalysisCard from './HazardAnalysisCard';
import '../layouts/cockpit-shell.css';
import './annunciator.css';

/**
 * Annunciator — the windshield HUD overlay, rendered as the canonical SLIM
 * LAMP STRIP:
 *
 *   [ALERT] · HAZARD  LAW  THREAT  COMM
 *
 * — one slim horizontal strip, reproduced from the ratified prototype's own
 * `.annun` markup (audit/design-briefs/cockpit-redesign-v10-RATIFIED.html
 * §05 L513-515/L631, :1203-1213) rather than the earlier bordered-box form.
 * IDLE lamps render as barely-visible dark chips; LIT lamps colorize by
 * severity. All 5 buttons (1 master bulb + 4 segments) are ALWAYS mounted
 * — unlike the prior sub-part, which only rendered a chip when active — so
 * the strip reads as a persistent instrument, not a conditional toast rail.
 *
 * WO-HUD-LIGHTS phase 1: the prior two-flank layout (WARN bulb · segs ·
 * CAUT bulb) is consolidated to a single leading `ALERT` master bulb — see
 * useAnnunciatorState.ts's own doc-comment for the full trigger rewire
 * (LAW/THREAT now read live sector-contact classification via
 * `components/tactical/contactClassification.ts`; COMM reads persistent
 * unread-count; TURNS removed). The master's CSS profile intentionally
 * reuses the old WARN bulb's `.bulb.warn.on`/`.bulb.warn.ack` rules
 * (cockpit-shell.css) rather than adding a new `.bulb.alert` rule — a
 * single consolidated master is, by construction, the most-urgent tier, so
 * MasterBulbButton below hardcodes the `warn` severity class.
 *
 * WO-UI0-SHELL-TRANSPLANT (leaf L5): the DOM emits the artifact's own BARE
 * classnames (`.annun`/`.lamp`/`.bulb`/`.segs`/`.seg`) — cockpit-shell.css
 * (the transplant's untouched CSS baseline, imported alongside this file's
 * own annunciator.css) owns the strip's geometry AND severity/state colors
 * (`.bulb.warn.on`, `.seg.live` etc) 1:1 from the ratified prototype;
 * annunciator.css keeps only what cockpit-shell.css doesn't cover — the
 * `.annunciator-overlay` scene-narrowing wrapper (below) and the
 * HazardAnalysisCard dialog skin. `.annun`'s flex layout (no
 * justify-content:space-between) re-centers around whatever content it
 * holds, so dropping the second (trailing) master bulb needed no CSS edit.
 *
 * ONE ack-able MASTER bulb flanks four click-through SEGMENTS
 * (HAZARD·LAW·THREAT·COMM) that are pure state indicators + navigators —
 * only the bulb silences a flash; segments always reflect live state
 * (mirrors the prototype: only `.bulb` has an ack onclick, `.seg` buttons
 * navigate).
 *
 * All trigger/lifecycle/navigation logic lives in useAnnunciatorState.ts
 * (shared with AnnunciatorMini.tsx) — see that file's doc-comment for the
 * full state-source table and the click-through mechanism per lamp.
 *
 * Overlay contract (SCENE-NARROWING guardrail), unchanged: the wrapper's
 * pointer-events:none is set INLINE (testable in this project's node/jsdom
 * vitest environment, which does not process CSS imports) and repeated in
 * annunciator.css; position:absolute/inset:0 so it contributes zero layout
 * size to its parent (GameLayout's `.band`); only interactive chrome (lamp
 * buttons, the hazard card) opts back into pointer-events.
 */

const MASTER_LABEL: Record<MasterBulb['id'], string> = { ALERT: 'ALERT' };

// role/aria-live are STATIC per severity (present on every render, active
// or idle) -- see roleFor/ariaLiveFor's own doc-comment in
// useAnnunciatorState.ts (the Pixel a11y fix-pass rationale, and why the
// helpers are shared with AnnunciatorMini.tsx rather than duplicated).

/* Bare artifact markup (RATIFIED.html:1204/1212): a blank `.bulb` button —
 * no icon, no text INSIDE it — followed by its ALERT label as a plain text
 * sibling inside `.lamp` (cockpit-shell.css styles that text via `.lamp`'s
 * own inherited font-size/color/letter-spacing, no dedicated label class
 * needed). Severity class is hardcoded `warn` (see the file doc-comment —
 * reuses the old WARN bulb's CSS profile for the single consolidated
 * master, no new `.bulb.alert` rule needed). */
const MasterBulbButton: React.FC<{ bulb: MasterBulb; reducedMotion: boolean }> = ({ bulb, reducedMotion }) => {
  const stateClass = bulb.active ? (bulb.flashing && !reducedMotion ? 'on' : 'ack') : '';
  return (
    <div className="lamp">
      <button
        type="button"
        className={['bulb', 'warn', stateClass].filter(Boolean).join(' ')}
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
  const { alert, segments, reducedMotion, hazardCardOpen, closeHazardCard, currentSector } =
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
        <MasterBulbButton bulb={alert} reducedMotion={reducedMotion} />
        <div className="segs">
          {segments.map((segment) => (
            <SegmentButton key={segment.id} segment={segment} ref={segment.id === 'HAZARD' ? hazardButtonRef : undefined} />
          ))}
        </div>
      </div>
      {hazardCardOpen && <HazardAnalysisCard sector={currentSector} onClose={handleCloseHazardCard} />}
    </div>
  );
};

export default Annunciator;
