/**
 * AutopilotContext — ADR-0072 Phase 1 client-side autopilot.
 *
 * Implements the frozen contract:
 *   plotCourse(target)  → POST /api/v1/nav/plot
 *   engage()            → executes course via moveToSector, one hop per
 *                         ~800ms setTimeout chain (cancellable)
 *   abort(reason)       → pauses with a reason string
 *
 * Status machine:
 *   idle ──plotCourse──► plotting ──success──► idle (course set)
 *   idle/paused ──engage──► engaged ──hop done──► engaged (next hop)
 *   engaged ──arrived──► arrived
 *   engaged ──error/manual──► paused (pauseReason set)
 *   any ──abort()──► paused / idle (no course)
 *
 * Session-bound: no persistence, unmount cancels all timers.
 * AutopilotProvider must mount INSIDE GameProvider scope.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from 'react';
import apiClient from '../services/apiClient';
import { useGame } from './GameContext';

// ── Types ──────────────────────────────────────────────────────────────────

export interface CourseHop {
  sector_id: number;
  name: string;
  turn_cost: number;
  visited: boolean;
  safety_rating: number | null;
  via_tunnel: boolean;
}

export interface CourseReachable {
  success: true;
  reachable: true;
  target_sector_id: number;
  hops: CourseHop[];
  total_turns: number;
}

export interface CourseUnreachable {
  success: true;
  reachable: false;
  target_sector_id: number;
  nearest_known: { sector_id: number; name: string } | null;
}

export type CoursePlot = CourseReachable | CourseUnreachable;

export type AutopilotStatus =
  | 'idle'
  | 'plotting'
  | 'engaged'
  | 'paused'
  | 'arrived';

export interface AutopilotContextValue {
  /** Last successful reachable plot (null while unset or unreachable). */
  course: CourseReachable | null;
  /** Last plot result including unreachable responses (null before first plot). */
  lastPlot: CoursePlot | null;
  status: AutopilotStatus;
  pauseReason: string | null;
  currentHopIndex: number;
  plotCourse: (targetSectorId: number) => Promise<void>;
  engage: () => void;
  abort: (reason: string) => void;
}

// ── Context ────────────────────────────────────────────────────────────────

const AutopilotContext = createContext<AutopilotContextValue | undefined>(
  undefined,
);

// ── Provider ───────────────────────────────────────────────────────────────

export const AutopilotProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const { moveToSector, playerState } = useGame();

  const [course, setCourse] = useState<CourseReachable | null>(null);
  const [lastPlot, setLastPlot] = useState<CoursePlot | null>(null);
  const [status, setStatus] = useState<AutopilotStatus>('idle');
  const [pauseReason, setPauseReason] = useState<string | null>(null);
  const [currentHopIndex, setCurrentHopIndex] = useState(0);

  // Timer handle for cancellation — the ref persists across renders without
  // causing re-renders itself.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Cancellation flag: the active hop chain reads this; if true it stops.
  const cancelledRef = useRef(false);
  // Ref to the latest course so the setTimeout chain always reads the current
  // value even after re-renders.
  const courseRef = useRef<CourseReachable | null>(null);
  const hopIndexRef = useRef(0);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  // Unmount cleanup
  React.useEffect(
    () => () => {
      cancelledRef.current = true;
      clearTimer();
    },
    [],
  );

  // ── plotCourse ─────────────────────────────────────────────────────────

  const plotCourse = useCallback(
    async (targetSectorId: number) => {
      // Abort any running course first
      cancelledRef.current = true;
      clearTimer();

      setStatus('plotting');
      setPauseReason(null);
      setCurrentHopIndex(0);
      hopIndexRef.current = 0;

      try {
        const response = await apiClient.post<CoursePlot>('/api/v1/nav/plot', {
          target_sector_id: targetSectorId,
          objective: 'min_time',
        });
        const plot = response.data;
        setLastPlot(plot);

        if (plot.reachable) {
          courseRef.current = plot;
          setCourse(plot);
          setCurrentHopIndex(0);
          hopIndexRef.current = 0;
          setStatus('idle');
        } else {
          // Unreachable — keep any prior course, surface the refusal via lastPlot
          setStatus('idle');
        }
      } catch {
        setStatus('idle');
      }
    },
    [],
  );

  // ── engage ─────────────────────────────────────────────────────────────

  const engage = useCallback(() => {
    const activeCourse = courseRef.current;
    if (!activeCourse || activeCourse.hops.length === 0) return;
    if (hopIndexRef.current >= activeCourse.hops.length) return;

    cancelledRef.current = false;
    clearTimer();
    setStatus('engaged');
    setPauseReason(null);

    const executeHop = () => {
      if (cancelledRef.current) return;

      const c = courseRef.current;
      if (!c) return;

      const idx = hopIndexRef.current;
      if (idx >= c.hops.length) {
        // All hops done — arrived
        setStatus('arrived');
        return;
      }

      const hop = c.hops[idx];
      const targetId = hop.sector_id;

      moveToSector(targetId)
        .then((result: any) => {
          if (cancelledRef.current) return;

          // The move endpoint returns 200 with success=false for game-logic
          // refusals (insufficient turns, no valid path). A refusal PAUSES
          // the course (player can re-engage after turns regen) — it does
          // not invalidate it.
          if (result && result.success === false) {
            setStatus('paused');
            setPauseReason(result.message || 'Movement refused');
            return;
          }

          // Bounce check — MoveResponse carries the landing sector as
          // new_sector_id. A landing that differs from the plotted hop
          // (latent one-way bounce, redirect) invalidates the course.
          const landedAt: number | undefined = result?.new_sector_id;

          if (landedAt !== undefined && landedAt !== null && landedAt !== targetId) {
            setStatus('paused');
            setPauseReason(
              `Expected sector ${targetId} but arrived at ${landedAt} — course invalidated`,
            );
            courseRef.current = null;
            setCourse(null);
            return;
          }

          // Encounter pause — MoveResponse.encounters (the movement service
          // attaches these; the response model passes them through).
          const hasEncounter =
            Array.isArray(result?.encounters) && result.encounters.length > 0;

          if (hasEncounter) {
            setStatus('paused');
            setPauseReason('Encounter detected — autopilot paused');
            return;
          }

          // Advance hop index
          const nextIdx = idx + 1;
          hopIndexRef.current = nextIdx;
          setCurrentHopIndex(nextIdx);

          if (cancelledRef.current) return;

          if (nextIdx >= (courseRef.current?.hops.length ?? 0)) {
            setStatus('arrived');
            return;
          }

          // Schedule next hop ~800ms later
          timerRef.current = setTimeout(executeHop, 800);
        })
        .catch((err: any) => {
          if (cancelledRef.current) return;
          const msg: string =
            err?.response?.data?.detail ||
            err?.message ||
            'Movement failed';
          setStatus('paused');
          setPauseReason(msg);
        });
    };

    // Kick off immediately (no leading delay — the user just pressed engage)
    executeHop();
  }, [moveToSector]);

  // ── abort ──────────────────────────────────────────────────────────────

  const abort = useCallback((reason: string) => {
    cancelledRef.current = true;
    clearTimer();
    if (courseRef.current) {
      setStatus('paused');
      setPauseReason(reason);
    } else {
      setStatus('idle');
      setPauseReason(null);
    }
  }, []);

  // Keep playerState dependency alive so the provider re-renders when the
  // player moves (which may externally clear the course expectation).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _ps = playerState;

  const value: AutopilotContextValue = {
    course,
    lastPlot,
    status,
    pauseReason,
    currentHopIndex,
    plotCourse,
    engage,
    abort,
  };

  return (
    <AutopilotContext.Provider value={value}>
      {children}
    </AutopilotContext.Provider>
  );
};

// ── Hook ───────────────────────────────────────────────────────────────────

export const useAutopilot = (): AutopilotContextValue => {
  const ctx = useContext(AutopilotContext);
  if (!ctx) {
    throw new Error('useAutopilot must be used within an AutopilotProvider');
  }
  return ctx;
};
