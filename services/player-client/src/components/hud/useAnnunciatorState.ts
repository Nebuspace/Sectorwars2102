import { useEffect, useRef, useState } from 'react';
import { useGame, type Sector } from '../../contexts/GameContext';
import { useMFD } from '../mfd/MFDContext';
import { planetaryAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import { requestTacticalPage } from '../../services/deckNavBus';
import {
  useSectorContacts,
  isLawArchetype,
  repBucket,
} from '../tactical/contactClassification';

/**
 * useAnnunciatorState — the annunciator's shared trigger/lifecycle/
 * navigation logic, consumed identically by the full windshield strip
 * (Annunciator.tsx) and the compact header variant (AnnunciatorMini.tsx) so
 * both read one state machine rather than two independently-computed
 * copies.
 *
 * WO-HUD-LIGHTS phase 1 REWIRE (supersedes WO-UI1-CHROME-COMPLETE /
 * WO-UI0-SHELL-TRANSPLANT's slim-lamp-strip build below this line of
 * history): the canonical form is now —
 *   [ALERT]  HAZARD  LAW  THREAT  COMM
 * — ONE ack-only master (ALERT) flanking four click-through SEGMENTS, TURNS
 * removed entirely. Triggers:
 *   HAZARD (caution) — unchanged: currentSector.hazard_level >= 5
 *                       (HAZARD_ACTIVE_THRESHOLD, carried over from
 *                       WO-UI0-SHELL-TRANSPLANT NIT n1).
 *   LAW    (caution) — FROM the player's own grey/fine status
 *                       (greyStatusAPI.isGrey, REST poll) TO a live sensor
 *                       read: any contactClassification.SectorContact
 *                       currently in-sector whose archetype is in the
 *                       LAW_ARCHETYPES set (LAW_ENFORCEMENT/FACTION_PATROL/
 *                       STATION_SECURITY, mirrors SolarSystemViewscreen's
 *                       shipFaction()). No more REST poll — sourced from the
 *                       same context-level sector-presence data
 *                       GameDashboard already merges.
 *   THREAT (warn)    — FROM the shipped COMBAT event lamp (WebSocketContext.
 *                       npcCombatSignal, edge-triggered/dwell) TO a live
 *                       sensor read: any sector contact whose repBucket is
 *                       red (WANTED) or gray (GREY-FLAG) — i.e. "a hostile
 *                       or suspicious contact is in the sector," not "you
 *                       are personally, currently fighting." Also sourced
 *                       from contactClassification, no event/dwell timer.
 *   COMM   (info)    — FROM the transient 15s event-flash (WebSocketContext.
 *                       newMessageSignal, edge-triggered) TO a persistent
 *                       level read: GameContext.unreadMessageCount > 0.
 *                       Pulsing is free — `.seg.livecm` (COMM's lit class,
 *                       cockpit-shell.css) already carries a CSS `flash`
 *                       animation, so no JS-level flash/dwell plumbing is
 *                       needed; the segment simply reflects the live count
 *                       and clears itself the instant something else in the
 *                       app (CommsCrewPage's own markMessageRead call on a
 *                       message row) drops the count to zero — no ack call
 *                       is fired from here.
 *   TURNS            — REMOVED. No segment, no master contribution, no
 *                       narration hook.
 *
 * RESTORED (hub ruling, WO-HUD-LIGHTS phase 1 follow-up): the first pass of
 * this rewrite dropped two pre-existing, segment-less WARN-only triggers —
 * flagged, not silently decided — and the hub ruled to PRESERVE them
 * (regression-prevention). Both are back, feeding ONLY the ALERT master,
 * exactly their old WARN-bulb-only shape — neither gets a segment, a
 * display row, or a click-through:
 *   siege  — an owned planet under siege, polled via the SAME
 *            planetaryAPI.getOwnedPlanets() call ColoniesRosterTab.tsx
 *            already makes (useSiegePoll below), `.some(p=>p.underSiege)`.
 *   bounty — playerState.bounty_total > 0, the SAME field + threshold
 *            StatusBar.tsx's BOUNTY chip uses.
 * It is CORRECT and INTENDED that the ALERT master can light with ZERO
 * segments lit when only siege or bounty is active — these are off-panel
 * conditions by design, exactly how the old WARN bulb behaved before this
 * rewrite. Do not "fix" that asymmetry.
 *
 * Master consolidation: the prior two-tier WARN (red/fast) + CAUTION
 * (amber/slow) master bulbs are replaced by ONE `ALERT` master, active
 * whenever ANY of the four segments OR siege OR bounty is active. It
 * renders with the old WARN bulb's CSS profile (`.bulb.warn.on`/
 * `.bulb.warn.ack`, cockpit-shell.css) — reused as-is rather than adding a
 * new `.bulb.alert` rule, since a single consolidated master is, by
 * construction, always the most-urgent tier; see Annunciator.tsx's
 * MasterBulbButton. Ack/flash lifecycle (useLevelLamp below) is unchanged
 * from the old per-bulb behavior: tap silences the flash without hiding the
 * lamp, and a fresh false→true edge re-arms the flash even after a prior
 * ack.
 *
 * Click-through: HAZARD opens the self-contained analysis popover
 * (HazardAnalysisCard.tsx). LAW/THREAT request the deck's TACTICAL softkey
 * via services/deckNavBus.ts (unchanged mechanism, new predicates). COMM
 * selects the MFD-B "comms-crew" page via useMFD().selectPage, fired at
 * BOTH possible screenIds ('sidebar-b' / the teleprinter mid-panel fold's
 * 'sidebar-a-folded') since only one is registered at a time and
 * MFDContext's SELECT_PAGE no-ops harmlessly on an unregistered screenId —
 * unchanged from the prior sub-part, minus the ack call (there is no longer
 * a single message id to ack; reading happens in CommsCrewPage itself).
 */

export type MasterBulbId = 'ALERT';
export type SegmentId = 'HAZARD' | 'LAW' | 'THREAT' | 'COMM';
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

// Bare artifact lit-classes (cockpit-shell.css): `.seg.live` = red
// (warn-tier), `.seg.livec` = amber (caution-tier), `.seg.livecm` = cyan
// (info/comm-tier). Shared by Annunciator.tsx AND AnnunciatorMini.tsx so
// the two views can never drift on which class a given segment lights up
// with.
const SEG_LIT_CLASS: Record<LampSeverity, 'live' | 'livec' | 'livecm'> = {
  warn: 'live',
  caution: 'livec',
  info: 'livecm',
};

/** WO-HUD-LIGHTS phase 1: LAW's prior NIT n5 special-case (forcing the
 *  WARN-red `.live` class regardless of its own `caution` severity) is
 *  RETIRED here — that special-case existed purely to match the ratified
 *  prototype's literal `G.fine>0` rendering, a condition that no longer
 *  exists (LAW no longer reflects the player's own fine/grey status at
 *  all). LAW now renders its natural caution-tier `.livec` (amber) like any
 *  other caution segment — "a lawman is nearby" reads as a caution, not a
 *  personal red alert. No segment special-cases its class anymore; kept as
 *  a named function (not inlined) so a future segment can reintroduce a
 *  demo-truth override in one place if needed. */
export function segLitClass(segment: Segment): 'live' | 'livec' | 'livecm' {
  return SEG_LIT_CLASS[segment.severity];
}

/* Pixel a11y fix-pass (WCAG 4.1.3-adjacent SR-transition defect): role and
 * aria-live must be STATIC (present on every render, active or idle), never
 * added/removed in step with `active` -- some screen readers only pick up a
 * live region's content changes if the region (and its role) was ALREADY
 * present in the accessibility tree before the change, so toggling presence
 * could silently eat the very transition the live region exists to
 * announce. Only the aria-label CONTENT changes to convey state
 * (describeMaster/describeSegment above already build full state text
 * either way, so this is a pure attribute-stability contract). Accepted
 * trade-off: an idle WARN-severity element (the ALERT master, a THREAT
 * segment) permanently carries role="alert" -- most screen readers only
 * announce role=alert content on a CHANGE, not merely being present/
 * mounted, so this does not spam idle mount; if a specific AT is found to
 * over-announce, revisit per-lamp.
 *
 * Shared here (not duplicated in Annunciator.tsx AND AnnunciatorMini.tsx)
 * so the two views can never drift on which severity maps to which
 * role/aria-live pair -- same rationale as `segLitClass` above. */
export const roleFor = (severity: LampSeverity | MasterBulbId): 'alert' | 'status' =>
  severity === 'warn' || severity === 'ALERT' ? 'alert' : 'status';
export const ariaLiveFor = (severity: LampSeverity | MasterBulbId): 'assertive' | 'polite' =>
  severity === 'warn' || severity === 'ALERT' ? 'assertive' : 'polite';

export interface AnnunciatorState {
  alert: MasterBulb;
  segments: Segment[];
  reducedMotion: boolean;
  hazardCardOpen: boolean;
  closeHazardCard: () => void;
  currentSector: Sector | null;
}

// WO-UI0-SHELL-TRANSPLANT NIT n1 (carried over unchanged): the ratified
// prototype's own lampState() threshold for hazard.
const HAZARD_ACTIVE_THRESHOLD = 5;

// NO-CANON, flagged for ratification (mirrors GameContext's own
// SECTOR_PRESENCE_POLL_MS=5000 flagged-cadence idiom): siege has no push
// signal — planetaryAPI is REST-only — so this hook polls it on an
// interval while mounted. Named SIEGE_POLL_MS (not the pre-restore
// LAW_SIEGE_POLL_MS) since LAW itself is no longer polled — only siege
// still is.
const SIEGE_POLL_MS = 20000;

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

/** Siege-on-your-colony — REST poll over the SAME endpoint ColoniesRosterTab
 * already calls (gameAPI.planetary.getOwnedPlanets → GET /api/v1/planets/
 * owned), reduced to a single boolean. Restored by hub ruling (see this
 * file's own doc-comment) — feeds ONLY the ALERT master, no segment. */
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
    const id = window.setInterval(fetchOwned, SIEGE_POLL_MS);
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
  const { playerState, currentSector, unreadMessageCount } = useGame();
  const { selectPage } = useMFD();
  const reducedMotion = useReducedMotion();

  const contacts = useSectorContacts();

  const hazardActive = !!currentSector && currentSector.hazard_level >= HAZARD_ACTIVE_THRESHOLD;
  const lawActive = contacts.some(isLawArchetype);
  const threatActive = contacts.some((c) => {
    const bucket = repBucket(c);
    return bucket === 'red' || bucket === 'gray';
  });
  const commActive = unreadMessageCount > 0;

  // Master-only, no segment — restored by hub ruling (see doc-comment).
  const bountyActive = !!playerState && (playerState.bounty_total ?? 0) > 0;
  const siegeActive = useSiegePoll(!!playerState);

  const alertLevel = useLevelLamp(
    hazardActive || lawActive || threatActive || commActive || siegeActive || bountyActive
  );

  const [hazardCardOpen, setHazardCardOpen] = useState(false);

  const openComm = () => {
    // The teleprinter's mid-panel mode folds MFD-B into MFD-A's rail under
    // a DISTINCT screenId (sidebarScreens.ts's own doc-comment on
    // SIDEBAR_A_FOLDED) -- only one of these two screenIds is registered at
    // a time; MFDContext's SELECT_PAGE reducer case no-ops on the other.
    selectPage('sidebar-b', 'comms-crew');
    selectPage('sidebar-a-folded', 'comms-crew');
  };

  const alert: MasterBulb = {
    id: 'ALERT',
    active: alertLevel.active,
    flashing: alertLevel.flashing,
    ack: alertLevel.ack,
    ariaLabel: describeMaster('Master alert', alertLevel.active, alertLevel.acked),
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
      ariaLabel: describeSegment('LAW', lawActive, 'law-enforcement contact in sector — navigates to TACTICAL, THREAT'),
      title: 'Law contact → TACTICAL, THREAT',
    },
    {
      id: 'THREAT',
      severity: 'warn',
      active: threatActive,
      onActivate: () => requestTacticalPage('target'),
      ariaLabel: describeSegment('THREAT', threatActive, 'wanted or grey-flagged contact in sector — navigates to TACTICAL, TARGET'),
      title: 'Contacts → TACTICAL, TARGET',
    },
    {
      id: 'COMM',
      severity: 'info',
      active: commActive,
      onActivate: openComm,
      ariaLabel: describeSegment('COMM', commActive, `${unreadMessageCount} unread message${unreadMessageCount === 1 ? '' : 's'} — opens the comms panel`),
      title: 'Unread traffic → COMM',
    },
  ];

  return {
    alert,
    segments,
    reducedMotion,
    hazardCardOpen,
    closeHazardCard: () => setHazardCardOpen(false),
    currentSector: currentSector ?? null,
  };
}
