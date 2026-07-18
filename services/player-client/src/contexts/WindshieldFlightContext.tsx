import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useAutopilot } from './AutopilotContext';
import type { PctPoint } from '../components/tactical/windshieldTableauLayout';
import { ENGAGE_RANGE_EM } from '../components/tactical/windshieldTableauHelpers';

/**
 * WindshieldFlightContext — the ONE shared flight-state store unifying the
 * windshield tableau's own intra-sector click→glide with the SOLAR SYSTEM
 * monitor's per-row APPROACH/HALT action and the glass locrow's ALL STOP
 * chip (WO-UI2-FLIGHT-FEEL seam fix).
 *
 * Row state machine (PlanetPortPair):
 *   APPROACH → (isFlying / HALT) → arrivedTargetId matches row → LAND/DOCK
 *   → confirm dialog → land/dock API.
 *
 * `arrivedTargetId` is set when a local glide completes naturally (not via
 * allStop). Cleared on a new approach() or allStop().
 */

export interface WindshieldFlightContextValue {
  /** True while the ship is visibly in transit — either the tableau's own
   *  local intra-sector glide, or a real autopilot course underway. */
  isFlying: boolean;
  /** planet_id/station_id of the current glide target, or null when idle. */
  targetId: string | null;
  /** planet_id/station_id the ship has parked at after a completed approach
   *  glide — SOLAR rows use this to flip APPROACH → LAND/DOCK without the
   *  player already being server-landed/docked. */
  arrivedTargetId: string | null;
  /** Row/locrow-facing: request the ship glide toward this body/station —
   *  the SAME glide a windshield band-object click performs. A no-op if no
   *  tableau is mounted (docked/landed) or the id can't be resolved once it
   *  is (e.g. a stale id from a sector that's since changed). */
  approach: (objectId: string) => void;
  /** Row/locrow-facing: cancel any in-progress local glide (the ship holds
   *  at its current on-screen position) AND abort a real autopilot course,
   *  if either is active — one control, both meanings of "flying". */
  allStop: () => void;

  // ---- Tableau-only wiring — do not call from rows/locrow/other UI. ----
  /** The pending approach request the mounted tableau should resolve and
   *  glide toward. A fresh object (new `seq`) on every `approach()` call,
   *  including repeat requests for the same id, so a keyed effect always
   *  re-fires. */
  pendingApproach: { objectId: string; seq: number } | null;
  /** Increments on every `allStop()` call — the tableau's own effect
   *  watches this to freeze its local glide at its live on-screen spot. */
  stopSignal: number;
  /** The tableau calls this after every local glide-state change (glide
   *  start, natural arrival, or a halt) so `isFlying`/`targetId` above stay
   *  in sync with the real, rendered glide. */
  reportFlightState: (localFlying: boolean, targetId: string | null) => void;

  // ---- Live position feed (WO-TACTICAL-APPROACH-ENGAGE-SCROLL Part B) ----
  /** The player's own ship position in canonical %-space, published by the
   *  mounted tableau every render tick — null before the tableau's first
   *  mount/measurement (or whenever no tableau is mounted, e.g. docked/
   *  landed). Proximity-gated UI (TACTICAL TARGET's Approach⇄Engage split)
   *  reads this directly rather than re-deriving flight geometry itself. */
  shipPos: PctPoint | null;
  /** Every OTHER contact's resolved on-screen position this tick, keyed by
   *  ship_id — the SAME resolution the tableau's own `.other` markers
   *  render from (server pose, cosmetic NPC wander, or the poseless-human
   *  parked anchor), so a consumer's proximity read never disagrees with
   *  where the dot is actually drawn. Empty when no tableau is mounted. */
  contactPositions: Map<string, PctPoint>;
  /** The tableau calls this every render tick with its own live ship
   *  position (mirrors reportFlightState). */
  reportShipPos: (pos: PctPoint | null) => void;
  /** The tableau calls this every render tick with every OTHER contact's
   *  resolved position, keyed by ship_id (mirrors reportFlightState). */
  reportContactPositions: (positions: Map<string, PctPoint>) => void;

  // ---- Server-published engage range (WO-API-A1) ----
  /** The server-authoritative ENGAGE proximity threshold (REFERENCE_BAND
   *  em) -- POST /combat/engage now enforces this SAME value, published on
   *  GET /sectors/{id}/contents (`engage_range_em`). Defaults to the local
   *  ENGAGE_RANGE_EM constant (windshieldTableauHelpers.tsx) until the
   *  tableau's first /contents fetch resolves, so TACTICAL TARGET's
   *  Approach/Engage split degrades to the pre-WO-API-A1 behavior for that
   *  brief pre-hydration window rather than reading `undefined`. The
   *  client-side check this drives is an OPTIMISTIC PREVIEW only -- the
   *  server independently re-derives and enforces the identical gate. */
  engageRangeEm: number;
  /** The tableau calls this once per /contents fetch with the server-
   *  published value (mirrors reportShipPos). */
  reportEngageRangeEm: (em: number) => void;
}

const WindshieldFlightContext = createContext<WindshieldFlightContextValue | undefined>(undefined);

export const WindshieldFlightProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const autopilot = useAutopilot();

  const [localFlying, setLocalFlying] = useState(false);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [arrivedTargetId, setArrivedTargetId] = useState<string | null>(null);
  const [pendingApproach, setPendingApproach] = useState<{ objectId: string; seq: number } | null>(null);
  const [stopSignal, setStopSignal] = useState(0);
  const [shipPos, setShipPos] = useState<PctPoint | null>(null);
  const [contactPositions, setContactPositions] = useState<Map<string, PctPoint>>(new Map());
  const [engageRangeEm, setEngageRangeEm] = useState<number>(ENGAGE_RANGE_EM);
  const approachSeqRef = useRef(0);
  const lastGlideTargetRef = useRef<string | null>(null);
  const wasLocalFlyingRef = useRef(false);
  const skipArrivalRef = useRef(false);

  const approach = useCallback((objectId: string) => {
    approachSeqRef.current += 1;
    skipArrivalRef.current = false;
    setArrivedTargetId(null);
    setPendingApproach({ objectId, seq: approachSeqRef.current });
  }, []);

  const allStop = useCallback(() => {
    skipArrivalRef.current = true;
    setArrivedTargetId(null);
    setStopSignal((n) => n + 1);
    autopilot.abort('all stop');
  }, [autopilot]);

  const reportFlightState = useCallback((flying: boolean, tgt: string | null) => {
    if (flying) {
      if (!wasLocalFlyingRef.current) {
        // Rising edge — new flight session. Allow arrive-on-settle unless
        // allStop later marks this session as a Halt (skipArrival stays set
        // through halt-turn/brake, which never drop through idle mid-way).
        skipArrivalRef.current = false;
        setArrivedTargetId(null);
        if (!tgt) lastGlideTargetRef.current = null;
      }
      if (tgt) lastGlideTargetRef.current = tgt;
    } else if (
      wasLocalFlyingRef.current
      && !skipArrivalRef.current
      && lastGlideTargetRef.current
    ) {
      // Natural end of a local glide — ship is parked at the approach point.
      setArrivedTargetId(lastGlideTargetRef.current);
    }
    wasLocalFlyingRef.current = flying;
    setLocalFlying(flying);
    setTargetId(tgt);
  }, []);

  const reportShipPos = useCallback((pos: PctPoint | null) => {
    setShipPos(pos);
  }, []);

  const reportContactPositions = useCallback((positions: Map<string, PctPoint>) => {
    setContactPositions(positions);
  }, []);

  const reportEngageRangeEm = useCallback((em: number) => {
    setEngageRangeEm(em);
  }, []);

  const value = useMemo<WindshieldFlightContextValue>(() => ({
    isFlying: localFlying || autopilot.status === 'engaged',
    targetId,
    arrivedTargetId,
    approach,
    allStop,
    pendingApproach,
    stopSignal,
    reportFlightState,
    shipPos,
    contactPositions,
    reportShipPos,
    reportContactPositions,
    engageRangeEm,
    reportEngageRangeEm,
  }), [
    localFlying, autopilot.status, targetId, arrivedTargetId, approach, allStop,
    pendingApproach, stopSignal, reportFlightState, shipPos, contactPositions,
    reportShipPos, reportContactPositions, engageRangeEm, reportEngageRangeEm,
  ]);

  return (
    <WindshieldFlightContext.Provider value={value}>
      {children}
    </WindshieldFlightContext.Provider>
  );
};

export const useWindshieldFlight = (): WindshieldFlightContextValue => {
  const ctx = useContext(WindshieldFlightContext);
  if (!ctx) {
    throw new Error('useWindshieldFlight must be used within a WindshieldFlightProvider');
  }
  return ctx;
};
