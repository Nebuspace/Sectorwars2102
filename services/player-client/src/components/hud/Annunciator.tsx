import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';
import { TurnsIcon } from '../icons/TurnsIcon';
import './annunciator.css';

/**
 * Annunciator — the windshield HUD overlay (WO-UI1-ANNUNCIATOR sub-part a).
 *
 * Ratified law (audit/design-briefs/cockpit-redesign-v10-RATIFIED.html §05,
 * WO-UI1-ANNUNCIATOR): event states are FLASHING windshield HUD warnings,
 * never standing buttons. MASTER WARN (red/fast) is the danger-class lane,
 * MASTER CAUTION (amber/slow) the advisory-class lane, and COMM stays its
 * own info-class lane (cyan) — canon: "never sharing the danger lane."
 * Flash → tap-acknowledge → steady-while-true → auto-clear.
 *
 * REUSE, not invention (Step-0 verified against the current tree — no new
 * event bus, no new WS frame types):
 *   COMBAT (warn)    — WebSocketContext.npcCombatSignal / lastNpcCombatInitiated
 *                       (the shipped `npc_combat_initiated` frame), gated to
 *                       the DEFENDER match exactly like NpcCombatBanner's own
 *                       isDefender check — this lamp is the player's own
 *                       jeopardy, not sector chatter (a spectator still only
 *                       gets NpcCombatBanner's lighter toast, untouched here).
 *   HAZARD (caution) — GameContext.currentSector.hazard_level (0–10 scale —
 *                       the same field GameDashboard's retiring HAZARD chip
 *                       and ThreatPage's "HAZARD LVL" field read), trigger
 *                       >0, matching that existing chip's own condition.
 *   TURNS (caution)  — GameContext.playerState.turns, trigger <50 — the exact
 *                       canon threshold TurnEconomyPage already ships
 *                       (turns.md "Low-turn warning… design: <50"). The
 *                       dispatched card names this state "low-fuel"; there is
 *                       no ship-fuel gauge anywhere in Ship/PlayerState — turns
 *                       are the resource that actually gates travel, and
 *                       canon's own MASTER CAUTION segment name for it is
 *                       TURNS, so that is the label used here.
 *   COMM (info)      — WebSocketContext.newMessageSignal / lastNewMessage,
 *                       gated to delivery.includes('toast') &&
 *                       !delivery.includes('modal') — i.e. exactly the
 *                       "priority-hail toast for non-urgent" surface the WO
 *                       names as retiring INTO the annunciator. The urgent
 *                       admin modal (delivery includes 'modal') is untouched:
 *                       canon — "urgent admin modal STAYS."
 *
 * NOT wired — PROXIMITY: grepped the full player-client tree (Step-0); no
 * proximity / intra-system-position signal exists anywhere in GameContext or
 * WebSocketContext today. That state belongs to WO-UI2-INTRASYSTEM-MODEL
 * (BACKEND, design-first, unbuilt) — wiring a live PROXIMITY lamp now would
 * mean inventing a signal, which this WO explicitly forbids. The lamp list
 * below is a plain array, so adding a 5th definition once that model ships
 * is a single new entry, not a rewrite.
 *
 * NOT wired — BOUNTY-on-you / siege-on-your-colony (both WARN per the wider
 * ratified brief) and grey-flag/fine (CAUTION, the LAW segment — see
 * ThreatPage's greyStatus, a REST poll, not a push signal): all three are
 * real and reachable, but the dispatched card for this sub-part names
 * exactly 5 states (proximity·combat·hazard·low-fuel·hail). Flagged as a
 * natural follow-up rather than built here unasked.
 *
 * Two lamp lifecycles:
 *   LEVEL lamps (HAZARD, TURNS) — `active` mirrors a continuous predicate.
 *     "Resolving the state clears it" happens for free: the predicate goes
 *     false, the tile unmounts. A tap only silences the flash early
 *     (steady-while-true); a fresh false→true edge re-flashes even if a
 *     prior occurrence was acked.
 *   EVENT lamps (COMBAT, COMM) — edge-triggered off a monotonic WS signal.
 *     Verified there is no combat_resolved (or equivalent) frame — combat
 *     today is the synchronous REST engage/resolve flow, not a live WS round
 *     stream (see NpcCombatBanner's own doc-comment). Mirrors MedalToast's
 *     proven self-clearing idiom instead: flash on the new signal, tap to
 *     ack (steady), auto-clear after a fixed dwell — "transient warnings, no
 *     standing buttons."
 *
 * Overlay contract (SCENE-NARROWING guardrail): the wrapper's
 * pointer-events:none is set INLINE (not only in annunciator.css) so it is
 * guaranteed testable in this project's node/jsdom vitest environment, which
 * does not process CSS imports — see Annunciator.test.tsx. It is
 * position:absolute / inset:0 (annunciator.css) so it contributes zero
 * layout size to its parent; only an active lamp tile gets pointer-events:
 * auto. Designed to be dropped as a sibling inside whatever positioned
 * windshield container the GameLayout slot-stitch (a separate step, run
 * later combined with the teleprinter stitch — NOT this sub-part) mounts it
 * into; a position:relative ancestor is a precondition this component
 * assumes but does not itself provide (mirrors game-layout.css's existing
 * .viewport-loading-overlay inset:0 convention).
 */

export type AnnunciatorSeverity = 'warn' | 'caution' | 'info';
export type AnnunciatorLampId = 'COMBAT' | 'HAZARD' | 'TURNS' | 'COMM';

interface RenderedLamp {
  id: AnnunciatorLampId;
  severity: AnnunciatorSeverity;
  label: string;
  icon: React.ReactNode;
  flashing: boolean;
  onAck: () => void;
}

// GameDashboard's retiring HAZARD chip triggers on `hazard_level > 0`
// (GameDashboard.tsx) — reused verbatim rather than inventing a new number.
const HAZARD_ACTIVE_THRESHOLD = 0;

// turns.md "Low-turn warning UI hints when the pool is below thresholds
// (design: <50)" — the exact threshold TurnEconomyPage.tsx already ships
// (`const lowTurns = playerState.turns < 50`).
const LOW_TURNS_THRESHOLD = 50;

// NO-CANON, flagged for ratification (mirrors the already-shipped
// UPLINK_TOAST_DEBOUNCE_MS / MedalToast.VISIBLE_MS flagged-constant idiom):
// how long a one-shot event lamp (no live "still true" signal to poll)
// stays up before it auto-clears on its own, absent an ack.
const EVENT_DWELL_MS = 15000;

/** Live prefers-reduced-motion tracking — mirrors SolarSystemViewscreen's
 * established useState+matchMedia pattern (duplicated locally rather than
 * extracted to a shared hook: extracting would mean editing that file,
 * which is out of this sub-part's scope). */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return reduced;
}

/** LEVEL lamp: active mirrors a continuous predicate; ack only silences the
 * flash while it stays true, and re-arms on the next false→true edge. */
function useLevelLamp(active: boolean) {
  const [acked, setAcked] = useState(false);
  const prevActiveRef = useRef(active);

  useEffect(() => {
    if (active && !prevActiveRef.current) {
      setAcked(false); // fresh alarm — re-flash even if a prior occurrence was acked
    }
    prevActiveRef.current = active;
  }, [active]);

  return {
    visible: active,
    flashing: active && !acked,
    ack: () => setAcked(true),
  };
}

/** EVENT lamp: edge-triggered off a monotonic signal, self-clears after a
 * fixed dwell (mirrors MedalToast). `eligible` lets a caller gate which
 * occurrences actually arm the lamp (e.g. defender-only, or delivery-surface
 * filtering) without losing the "distinct signal value" de-dupe. */
function useEventLamp(signal: number, eligible: boolean, dwellMs: number) {
  const [visible, setVisible] = useState(false);
  const [acked, setAcked] = useState(false);
  const seenSignalRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (signal <= 0 || signal === seenSignalRef.current) return;
    seenSignalRef.current = signal;
    if (!eligible) return;

    setVisible(true);
    setAcked(false);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setVisible(false), dwellMs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal, eligible]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    []
  );

  return {
    visible,
    flashing: visible && !acked,
    ack: () => setAcked(true),
  };
}

const Annunciator: React.FC = () => {
  const { npcCombatSignal, lastNpcCombatInitiated, newMessageSignal, lastNewMessage } = useWebSocket();
  const { playerState, currentSector, markMessageRead } = useGame();
  const reducedMotion = useReducedMotion();

  const isDefender = !!playerState && lastNpcCombatInitiated?.defender_id === playerState.id;
  const combat = useEventLamp(npcCombatSignal, isDefender, EVENT_DWELL_MS);

  const commEligible =
    !!lastNewMessage && lastNewMessage.delivery.includes('toast') && !lastNewMessage.delivery.includes('modal');
  const comm = useEventLamp(newMessageSignal, commEligible, EVENT_DWELL_MS);
  // Latch the message id at the moment the lamp arms so a LATER inbound
  // message (which reassigns lastNewMessage/newMessageSignal) can't cause a
  // stale ack to mark the wrong hail read.
  const commMessageIdRef = useRef<string | null>(null);
  if (comm.visible && commEligible && lastNewMessage) {
    commMessageIdRef.current = lastNewMessage.message_id || null;
  }
  const ackComm = () => {
    comm.ack();
    const id = commMessageIdRef.current;
    if (id) {
      markMessageRead(id).catch((err) => console.warn('Annunciator: failed to mark hail read:', err));
    }
  };

  const hazardActive = !!currentSector && currentSector.hazard_level > HAZARD_ACTIVE_THRESHOLD;
  const hazard = useLevelLamp(hazardActive);

  const turnsActive = !!playerState && playerState.turns < LOW_TURNS_THRESHOLD;
  const turns = useLevelLamp(turnsActive);

  const lamps: RenderedLamp[] = [];
  if (combat.visible) {
    lamps.push({
      id: 'COMBAT',
      severity: 'warn',
      label: 'COMBAT',
      icon: <span aria-hidden="true">⚔</span>,
      flashing: combat.flashing,
      onAck: combat.ack,
    });
  }
  if (hazard.visible) {
    lamps.push({
      id: 'HAZARD',
      severity: 'caution',
      label: 'HAZARD',
      icon: <span aria-hidden="true">☢</span>,
      flashing: hazard.flashing,
      onAck: hazard.ack,
    });
  }
  if (turns.visible) {
    lamps.push({
      id: 'TURNS',
      severity: 'caution',
      label: 'TURNS',
      icon: <TurnsIcon aria-hidden="true" size="1.05em" />,
      flashing: turns.flashing,
      onAck: turns.ack,
    });
  }
  if (comm.visible) {
    lamps.push({
      id: 'COMM',
      severity: 'info',
      label: 'COMM',
      icon: <span aria-hidden="true">✉</span>,
      flashing: comm.flashing,
      onAck: ackComm,
    });
  }

  return (
    <div className="annunciator-overlay" style={{ pointerEvents: 'none' }} data-testid="annunciator-overlay">
      {lamps.map((lamp) => (
        <div
          key={lamp.id}
          role={lamp.severity === 'warn' ? 'alert' : 'status'}
          aria-live={lamp.severity === 'warn' ? 'assertive' : 'polite'}
          className={[
            'annunciator-lamp',
            `annunciator-lamp--${lamp.severity}`,
            lamp.flashing && !reducedMotion ? 'is-flashing' : 'is-steady',
            reducedMotion ? 'is-reduced-motion' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          style={{ pointerEvents: 'auto' }}
        >
          <span className="annunciator-lamp-icon">{lamp.icon}</span>
          <span className="annunciator-lamp-severity">{lamp.severity.toUpperCase()}</span>
          <span className="annunciator-lamp-label">{lamp.label}</span>
          <button
            type="button"
            className="annunciator-lamp-ack"
            onClick={lamp.onAck}
            aria-label={`Acknowledge ${lamp.label}`}
          >
            ✓
          </button>
        </div>
      ))}
    </div>
  );
};

export default Annunciator;
