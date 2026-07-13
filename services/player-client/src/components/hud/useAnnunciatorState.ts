import { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame, type Sector } from '../../contexts/GameContext';
import { useMFD } from '../mfd/MFDContext';
import { greyStatusAPI, planetaryAPI, type GreyStatus } from '../../services/api';
import type { Planet } from '../../types/planetary';
import { requestTacticalPage } from '../../services/deckNavBus';
import { ariaFeed } from '../mfd/ariaFeedStore';

/**
 * useAnnunciatorState — the annunciator's shared trigger/lifecycle/
 * navigation logic (WO-UI1-CHROME-COMPLETE), consumed identically by the
 * full windshield strip (Annunciator.tsx) and the compact header variant
 * (AnnunciatorMini.tsx) so both read one state machine rather than two
 * independently-computed copies. Mirrors the dual-poll precedent already
 * shipped in this codebase (ThreatPage.tsx AND TacticalThreatPage.tsx both
 * independently fetch greyStatusAPI.getStatus() on mount) rather than
 * inventing a new shared-poll cache: each mounted instance of this hook
 * polls independently, which is fine at this cadence and this call count.
 *
 * Ratified law (audit/design-briefs/cockpit-redesign-v10-RATIFIED.html §05
 * L513-515, L631, WO-UI1-ANNUNCIATOR/WO-UI1-CHROME-COMPLETE): the canonical
 * form is the SLIM LAMP STRIP —
 *   [WARN] · HAZARD  LAW  THREAT  TURNS  COMM · [CAUT]
 * — reproduced from the ratified prototype's own `renderBand()`/`.annun`
 * markup (RATIFIED.html:1203-1213) 1:1: two ack-only MASTER bulbs (WARN
 * red/fast, CAUTION amber/slow) flanking five click-through SEGMENTS that
 * are pure state indicators + navigators (no per-segment ack — only the
 * two master bulbs silence a flash; segments always reflect live state).
 *
 * DOC-GAP surfaced during the original build (flagged, not silently
 * resolved — status UPDATED below, not re-litigated): the ratified
 * prototype's OWN `renderBand()` gives the LAW segment the WARN-red
 * `.seg.live` CSS class when `G.fine>0` (RATIFIED.html:1207), but its OWN
 * `lampState()` boolean literally two lines above computes
 * `caut = sec.hazard>=5 || G.fine>0 || G.turns<50` (RATIFIED.html:1198) —
 * i.e. the SAME prototype file classifies grey-flag/fine as a CAUTION-tier
 * condition for the master bulb while visually coloring the LAW segment as
 * if it were WARN-tier. The prose canon (persistent-chrome tree L513-515:
 * "CAUTION lamp (amber/slow: hazard, low-turns, fine)"; WO-UI1-ANNUNCIATOR
 * GOAL: "MASTER CAUTION (amber/slow: hazard≥threshold, grey-flag/fine,
 * low-turns)") and the original WO's own Accept criterion (e) ("CAUTION on
 * grey-flag/fine") both agree with the BOOLEAN, not the CSS class — this
 * build originally went with the boolean for BOTH the master-bulb feed AND
 * the segment's own visual class (LAW rendered caution-amber throughout).
 *
 * WO-UI0-SHELL-TRANSPLANT (leaf L5) NIT n5 changed HALF of that: the shell
 * transplant's rule is "the demo's rendered truth wins by construction" for
 * cosmetic classnames, so LAW's SEGMENT now emits the demo's literal
 * `.seg.live` (red) class when active — see `segLitClass()` below, which
 * special-cases LAW to the WARN-tier lit class regardless of its own
 * `severity` field. The BOOLEAN side of the doc-gap is UNCHANGED and still
 * resolved per the original reasoning: LAW keeps `severity: 'caution'`
 * (feeds the CAUT master bulb, not WARN; drives `role="status"`/
 * `aria-live="polite"` for a11y — WCAG 4.1.3 state-not-color-alone), and
 * `lawActive` still only ever contributes to `cautionLevel`, never
 * `warnLevel`. The doc-gap itself — whether prose canon should be revised
 * to match the prototype's own visual choice — is staged for Max
 * separately, not resolved by this leaf.
 *
 * Triggers (REUSE, not invention — every field below already exists):
 *   THREAT (warn)    — the shipped COMBAT event lamp, unchanged: WebSocket-
 *                       Context.npcCombatSignal/lastNpcCombatInitiated,
 *                       gated to the DEFENDER match (this player's own
 *                       jeopardy). Feeds the segment AND the WARN bulb.
 *   siege  (warn, no segment of its own — canon lists it as a WARN-bulb
 *                       trigger only, not one of the 5 labeled segments) —
 *                       polled planetaryAPI.getOwnedPlanets() (the EXACT
 *                       call ColoniesRosterTab.tsx already makes for the
 *                       player-menu Colonies roster), `.some(p=>p.underSiege)`.
 *   bounty (warn, no segment) — playerState.bounty_total > 0, the SAME
 *                       field + threshold StatusBar.tsx's BOUNTY chip uses.
 *   HAZARD (caution)  — currentSector.hazard_level >= 5 (WO-UI0-SHELL-
 *                       TRANSPLANT NIT n1 — supersedes the prior sub-part's
 *                       `> 0`, which mirrored GameDashboard's retiring
 *                       HAZARD chip; the two are now DELIBERATELY divergent,
 *                       this hook follows the ratified prototype's own
 *                       lampState() threshold instead, see
 *                       HAZARD_ACTIVE_THRESHOLD below).
 *   LAW (caution)     — polled greyStatusAPI.getStatus() (the EXACT call
 *                       ThreatPage.tsx/TacticalThreatPage.tsx already make),
 *                       `.isGrey`.
 *   TURNS (caution)   — playerState.turns < 50 (unchanged — TurnEconomyPage's
 *                       own threshold).
 *   COMM (info)       — the shipped COMM event lamp, unchanged: newMessage-
 *                       Signal/lastNewMessage, toast-non-modal gate.
 *
 * Click-through (item 6): HAZARD opens a self-contained analysis popover
 * (real currentSector fields — see HazardAnalysisCard.tsx — no cross-file
 * reach needed). LAW/THREAT request the deck's TACTICAL softkey via the new
 * services/deckNavBus.ts (TacticalMonitor.tsx has zero shared nav surface
 * today — see that file's own doc-comment for why a small bus, not a prop,
 * is the minimal fix). COMM selects the MFD-B "comms-crew" page via
 * useMFD().selectPage AND clears the hail (existing markMessageRead call) —
 * fired at BOTH possible screenIds ('sidebar-b' / the teleprinter mid-panel
 * fold's 'sidebar-a-folded') since only one is registered at a time and
 * MFDContext's SELECT_PAGE no-ops harmlessly on an unregistered screenId.
 * TURNS has no owning surface (Accept criterion (f) deliberately excludes
 * it from the navigate set, matching the prototype's own `say()`-only
 * TURNS handler) — narrates the live turn count via the shared ariaFeed
 * store instead (the same channel GameLayout's MFDAlertWiring narrates
 * autopilot transitions into).
 */

export type MasterBulbId = 'WARN' | 'CAUT';
export type SegmentId = 'HAZARD' | 'LAW' | 'THREAT' | 'TURNS' | 'COMM';
export type LampSeverity = 'warn' | 'caution' | 'info';

export interface MasterBulb {
  id: MasterBulbId;
  active: boolean;
  flashing: boolean;
  ack: () => void;
  ariaLabel: string;
}

export interface Segment {
  id: SegmentId;
  severity: LampSeverity;
  active: boolean;
  onActivate: () => void;
  ariaLabel: string;
  title: string;
}

// Bare artifact lit-classes (cockpit-shell.css, WO-UI0-SHELL-TRANSPLANT):
// `.seg.live` = red (warn-tier), `.seg.livec` = amber (caution-tier),
// `.seg.livecm` = cyan (info/comm-tier). Shared by Annunciator.tsx AND
// AnnunciatorMini.tsx so the two views can never drift on which class a
// given segment lights up with.
const SEG_LIT_CLASS: Record<LampSeverity, 'live' | 'livec' | 'livecm'> = {
  warn: 'live',
  caution: 'livec',
  info: 'livecm',
};

/** NIT n5 (rendered-demo-truth wins, see the doc-gap note above): LAW is
 * `severity: 'caution'` for every LOGICAL purpose (feeds the CAUT master
 * bulb, gets the caution a11y role) but the ratified prototype's own
 * renderBand() visually lights its segment with the WARN-red `.seg.live`
 * class (RATIFIED.html:1207) — special-cased here rather than in either
 * consuming component, so both stay byte-identical. */
export function segLitClass(segment: Segment): 'live' | 'livec' | 'livecm' {
  return segment.id === 'LAW' ? 'live' : SEG_LIT_CLASS[segment.severity];
}

export interface AnnunciatorState {
  warn: MasterBulb;
  caution: MasterBulb;
  segments: Segment[];
  reducedMotion: boolean;
  hazardCardOpen: boolean;
  closeHazardCard: () => void;
  currentSector: Sector | null;
}

// WO-UI0-SHELL-TRANSPLANT NIT n1: the ratified prototype's OWN lampState()
// (RATIFIED.html:1198-1201, `caut = sec.hazard>=5 || G.fine>0 || G.turns<50`)
// is the rendered-demo-truth this transplant adopts for BOTH the HAZARD
// segment's own lit state and its contribution to the CAUT master bulb —
// supersedes the prior sub-part's `> 0` (which mirrored GameDashboard's
// retiring HAZARD chip; the two thresholds are now intentionally different).
const HAZARD_ACTIVE_THRESHOLD = 5;

// turns.md "Low-turn warning UI hints when the pool is below thresholds
// (design: <50)" — the exact threshold TurnEconomyPage.tsx already ships
// (`const lowTurns = playerState.turns < 50`).
const LOW_TURNS_THRESHOLD = 50;

// NO-CANON, flagged for ratification (mirrors the already-shipped
// UPLINK_TOAST_DEBOUNCE_MS / EVENT_DWELL_MS flagged-constant idiom): how
// long a one-shot event lamp (no live "still true" signal to poll) stays up
// before it auto-clears on its own, absent an ack.
const EVENT_DWELL_MS = 15000;

// NO-CANON, flagged for ratification (mirrors GameContext's own
// SECTOR_PRESENCE_POLL_MS=5000 flagged-cadence idiom): LAW (grey-flag) and
// siege have no push signal — greyStatusAPI/planetaryAPI are REST-only —
// so this hook polls both on an interval while mounted. Slower than the
// 5s sector-presence poll: neither condition needs sub-5s freshness, and
// two independently-mounted hook instances (main strip + a future mini)
// would otherwise double the request rate at a tighter cadence.
const LAW_SIEGE_POLL_MS = 20000;

/** Live prefers-reduced-motion tracking — mirrors SolarSystemViewscreen's
 * established useState+matchMedia pattern. */
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
    active,
    flashing: active && !acked,
    acked,
    ack: () => setAcked(true),
  };
}

/** EVENT lamp: edge-triggered off a monotonic signal, self-clears after a
 * fixed dwell (mirrors MedalToast). `eligible` gates which occurrences
 * actually arm the lamp without losing the "distinct signal value" de-dupe. */
function useEventLamp(signal: number, eligible: boolean, dwellMs: number) {
  const [visible, setVisible] = useState(false);
  const seenSignalRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (signal <= 0 || signal === seenSignalRef.current) return;
    seenSignalRef.current = signal;
    if (!eligible) return;

    setVisible(true);
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

  return { visible, clear: () => setVisible(false) };
}

/** LAW (grey-flag/fine) — REST poll, no push signal (see ThreatPage.tsx's
 * own doc-comment on greyStatusAPI). */
function useGreyStatusPoll(enabled: boolean): GreyStatus | null {
  const [status, setStatus] = useState<GreyStatus | null>(null);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const fetchStatus = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      greyStatusAPI
        .getStatus()
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch(() => {
          // Transient — the next tick retries (mirrors GameContext's
          // sector-presence poll silent-catch convention).
        });
    };
    fetchStatus();
    const id = window.setInterval(fetchStatus, LAW_SIEGE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled]);

  return status;
}

/** Siege-on-your-colony — REST poll over the SAME endpoint ColoniesRosterTab
 * already calls (gameAPI.planetary.getOwnedPlanets → GET /api/v1/planets/
 * owned), reduced to a single boolean. */
function useSiegePoll(enabled: boolean): boolean {
  const [underSiege, setUnderSiege] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const fetchOwned = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      planetaryAPI
        .getOwnedPlanets()
        .then((response: { planets?: Planet[] } | Planet[]) => {
          if (cancelled) return;
          const planets: Planet[] = Array.isArray(response) ? response : response?.planets || [];
          setUnderSiege(planets.some((p) => p.underSiege));
        })
        .catch(() => {
          // Transient — the next tick retries.
        });
    };
    fetchOwned();
    const id = window.setInterval(fetchOwned, LAW_SIEGE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled]);

  return underSiege;
}

const describeMaster = (label: string, active: boolean, acked: boolean): string => {
  if (!active) return `${label} — clear`;
  return acked ? `${label} — active, acknowledged` : `${label} — active, tap to acknowledge`;
};

const describeSegment = (label: string, active: boolean, activeDescription: string): string =>
  active ? `${label} segment — ${activeDescription}` : `${label} segment — clear`;

export function useAnnunciatorState(): AnnunciatorState {
  const { npcCombatSignal, lastNpcCombatInitiated, newMessageSignal, lastNewMessage } = useWebSocket();
  const { playerState, currentSector, markMessageRead } = useGame();
  const { selectPage } = useMFD();
  const reducedMotion = useReducedMotion();

  const isDefender = !!playerState && lastNpcCombatInitiated?.defender_id === playerState.id;
  const combat = useEventLamp(npcCombatSignal, isDefender, EVENT_DWELL_MS);

  const commEligible =
    !!lastNewMessage && lastNewMessage.delivery.includes('toast') && !lastNewMessage.delivery.includes('modal');
  const comm = useEventLamp(newMessageSignal, commEligible, EVENT_DWELL_MS);
  // Latch the message id at the moment the lamp arms so a LATER inbound
  // message (which reassigns lastNewMessage/newMessageSignal) can't cause a
  // stale click to mark the wrong hail read.
  const commMessageIdRef = useRef<string | null>(null);
  if (comm.visible && commEligible && lastNewMessage) {
    commMessageIdRef.current = lastNewMessage.message_id || null;
  }

  const hazardActive = !!currentSector && currentSector.hazard_level >= HAZARD_ACTIVE_THRESHOLD;
  const turnsActive = !!playerState && playerState.turns < LOW_TURNS_THRESHOLD;
  const bountyActive = !!playerState && (playerState.bounty_total ?? 0) > 0;

  const greyStatus = useGreyStatusPoll(!!playerState);
  const lawActive = !!greyStatus?.isGrey;

  const siegeActive = useSiegePoll(!!playerState);

  const warnLevel = useLevelLamp(combat.visible || siegeActive || bountyActive);
  const cautionLevel = useLevelLamp(hazardActive || lawActive || turnsActive);

  const [hazardCardOpen, setHazardCardOpen] = useState(false);

  const ackComm = () => {
    comm.clear();
    const id = commMessageIdRef.current;
    if (id) {
      markMessageRead(id).catch((err) => console.warn('Annunciator: failed to mark hail read:', err));
    }
  };

  const openComm = () => {
    // The teleprinter's mid-panel mode folds MFD-B into MFD-A's rail under
    // a DISTINCT screenId (sidebarScreens.ts's own doc-comment on
    // SIDEBAR_A_FOLDED) -- only one of these two screenIds is registered at
    // a time; MFDContext's SELECT_PAGE reducer case no-ops on the other.
    selectPage('sidebar-b', 'comms-crew');
    selectPage('sidebar-a-folded', 'comms-crew');
    ackComm();
  };

  const narrateTurns = () => {
    const turns = playerState?.turns ?? 0;
    ariaFeed.appendNav(
      turnsActive
        ? `Turn reserve: ${turns}. Running on fumes, Commander.`
        : `Turn reserve: ${turns}. Plenty in the tank.`
    );
  };

  const warn: MasterBulb = {
    id: 'WARN',
    active: warnLevel.active,
    flashing: warnLevel.flashing,
    ack: warnLevel.ack,
    ariaLabel: describeMaster('Master warning', warnLevel.active, warnLevel.acked),
  };

  const caution: MasterBulb = {
    id: 'CAUT',
    active: cautionLevel.active,
    flashing: cautionLevel.flashing,
    ack: cautionLevel.ack,
    ariaLabel: describeMaster('Master caution', cautionLevel.active, cautionLevel.acked),
  };

  const segments: Segment[] = [
    {
      id: 'HAZARD',
      severity: 'caution',
      active: hazardActive,
      onActivate: () => setHazardCardOpen(true),
      ariaLabel: describeSegment(
        'HAZARD',
        hazardActive,
        `hazard level ${currentSector?.hazard_level ?? 0} of 10 — opens the hazard analysis card`
      ),
      title: 'Hazard analysis',
    },
    {
      id: 'LAW',
      severity: 'caution',
      active: lawActive,
      onActivate: () => requestTacticalPage('threat'),
      ariaLabel: describeSegment('LAW', lawActive, 'grey-flag fine outstanding — navigates to TACTICAL, THREAT'),
      title: 'Law status → TACTICAL, THREAT',
    },
    {
      id: 'THREAT',
      severity: 'warn',
      active: combat.visible,
      onActivate: () => requestTacticalPage('target'),
      ariaLabel: describeSegment('THREAT', combat.visible, 'combat engaged — navigates to TACTICAL, TARGET'),
      title: 'Contacts → TACTICAL, TARGET',
    },
    {
      id: 'TURNS',
      severity: 'caution',
      active: turnsActive,
      onActivate: narrateTurns,
      ariaLabel: describeSegment('TURNS', turnsActive, `turn reserve low, ${playerState?.turns ?? 0} remaining`),
      title: 'Turn reserve',
    },
    {
      id: 'COMM',
      severity: 'info',
      active: comm.visible,
      onActivate: openComm,
      ariaLabel: describeSegment('COMM', comm.visible, 'incoming traffic — opens the comms panel'),
      title: 'Incoming traffic → COMM',
    },
  ];

  return {
    warn,
    caution,
    segments,
    reducedMotion,
    hazardCardOpen,
    closeHazardCard: () => setHazardCardOpen(false),
    currentSector: currentSector ?? null,
  };
}
