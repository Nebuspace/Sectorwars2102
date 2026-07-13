import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useAutopilot } from './AutopilotContext';

/**
 * WindshieldFlightContext — the ONE shared flight-state store unifying the
 * windshield tableau's own intra-sector click→glide with the SOLAR SYSTEM
 * monitor's per-row APPROACH/HALT action and the glass locrow's ALL STOP
 * chip (WO-UI2-FLIGHT-FEEL seam fix).
 *
 * ROOT CAUSE this replaces: the rows/locrow read `flying`/`allStop` off
 * `autopilot.status` (AutopilotContext's REAL inter-sector course engine),
 * while WindshieldTableau.tsx's own click-to-glide (its `travelTo`) is a
 * completely separate, purely-cosmetic intra-sector animation with no
 * connection to that status at all. A row's "APPROACH ▸" click never
 * reached the glide, the row never flipped to HALT while gliding, and the
 * locrow's ALL STOP chip never appeared for it either.
 *
 * ARCHITECTURE — the tableau OWNS the actual glide (it alone has the
 * fetched /contents system data needed to resolve a planet/station id to a
 * tableau %-position, and it alone renders + animates the `.shipmk` marker),
 * so this Provider is a small coordination bus, not a second copy of the
 * glide state:
 *   - Row/locrow-facing (`approach`/`allStop`/`isFlying`/`targetId`): the
 *     public API any consumer calls/reads.
 *   - Tableau-facing (`pendingApproach`/`stopSignal`/`reportFlightState`):
 *     internal wiring ONLY WindshieldTableau.tsx should touch — a row click
 *     records a request here; the mounted tableau's own effect resolves it
 *     against its system data and performs the real glide, then reports the
 *     resulting local flight state back so `isFlying`/`targetId` stay live.
 *
 * `isFlying` is `localFlying || autopilot.status === 'engaged'` — a
 * superset of the old (buggy) autopilot-only signal, so the existing "block
 * a row mid real inter-sector course" behavior the old code had is
 * preserved, not dropped, while the previously-missing local-glide half is
 * now ALSO covered. `allStop()` mirrors that union: it always aborts a real
 * autopilot course AND signals the tableau to freeze any local glide, so
 * one control genuinely halts whichever kind of "flying" is actually
 * happening.
 *
 * Mounted once, wrapping GameDashboard's whole tree (GameDashboard.tsx) so
 * it's a stable ancestor of both the windshield mount and the SOLAR SYSTEM
 * monitor / locrow siblings — see that file's outer/inner split.
 */

export interface WindshieldFlightContextValue {
  /** True while the ship is visibly in transit — either the tableau's own
   *  local intra-sector glide, or a real autopilot course underway. */
  isFlying: boolean;
  /** planet_id/station_id of the current glide target, or null when idle. */
  targetId: string | null;
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
}

const WindshieldFlightContext = createContext<WindshieldFlightContextValue | undefined>(undefined);

export const WindshieldFlightProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const autopilot = useAutopilot();

  const [localFlying, setLocalFlying] = useState(false);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [pendingApproach, setPendingApproach] = useState<{ objectId: string; seq: number } | null>(null);
  const [stopSignal, setStopSignal] = useState(0);
  const approachSeqRef = useRef(0);

  const approach = useCallback((objectId: string) => {
    approachSeqRef.current += 1;
    setPendingApproach({ objectId, seq: approachSeqRef.current });
  }, []);

  const allStop = useCallback(() => {
    setStopSignal((n) => n + 1);
    autopilot.abort('all stop');
  }, [autopilot]);

  const reportFlightState = useCallback((flying: boolean, tgt: string | null) => {
    setLocalFlying(flying);
    setTargetId(tgt);
  }, []);

  const value = useMemo<WindshieldFlightContextValue>(() => ({
    isFlying: localFlying || autopilot.status === 'engaged',
    targetId,
    approach,
    allStop,
    pendingApproach,
    stopSignal,
    reportFlightState,
  }), [localFlying, autopilot.status, targetId, approach, allStop, pendingApproach, stopSignal, reportFlightState]);

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
