/**
 * AutopilotHud — ARIA Autopilot course as a daisy-chained set of dots on a
 * line, from the originating sector to the destination. A cockpit HUD
 * element (NOT part of ARIA / the teleprinter). Visible only while a course
 * is actively being flown; it disappears the moment we arrive.
 */

import { useEffect, useRef, useState } from 'react';
import { useAutopilot } from '../../contexts/AutopilotContext';
import { useGame } from '../../contexts/GameContext';
import './autopilot-hud.css';

export type AutopilotHudNodeState = 'origin' | 'reached' | 'active' | 'pending' | 'destination';

export interface AutopilotHudNode {
  sector_id: number;
  name: string;
  /** dot state for styling */
  state: AutopilotHudNodeState;
  /** connector leading INTO this dot: traversed / in-transit / pending */
  legState: 'traversed' | 'active' | 'pending' | null;
}

/**
 * Pure layout: turn an origin + hop list + current leg index into an
 * ordered chain of dots with per-dot + per-connector state.
 *
 * `currentHopIndex` indexes `hops`: hops[currentHopIndex] is the leg being
 * flown right now (destination of the in-transit jump). Everything before
 * it is already reached.
 */
export function buildAutopilotChain(
  origin: { sector_id: number; name: string } | null,
  hops: { sector_id: number; name: string }[],
  currentHopIndex: number,
): AutopilotHudNode[] {
  const chain: AutopilotHudNode[] = [];
  const lastIdx = hops.length - 1;

  if (origin) {
    chain.push({
      sector_id: origin.sector_id,
      name: origin.name,
      state: 'origin',
      legState: null,
    });
  }

  hops.forEach((hop, i) => {
    // Dot i is reached once we've completed leg i (currentHopIndex has moved
    // past it). The leg INTO dot i is leg index i.
    const reached = i < currentHopIndex;
    const isTransitTarget = i === currentHopIndex;
    const state: AutopilotHudNodeState =
      i === lastIdx
        ? 'destination'
        : reached
          ? 'reached'
          : isTransitTarget
            ? 'active'
            : 'pending';
    const legState =
      i < currentHopIndex ? 'traversed' : i === currentHopIndex ? 'active' : 'pending';
    chain.push({
      sector_id: hop.sector_id,
      name: hop.name,
      state,
      legState,
    });
  });

  return chain;
}

export default function AutopilotHud() {
  const { course, status, currentHopIndex } = useAutopilot();
  const { currentSector } = useGame();

  // Capture the origin sector the instant a course starts flying, keyed on
  // course identity so a fresh plot re-captures. Origin is NOT in `hops`.
  const [origin, setOrigin] = useState<{ sector_id: number; name: string } | null>(null);
  const capturedKeyRef = useRef<string | null>(null);
  const courseKey = course ? `${course.target_sector_id}:${course.hops.length}` : null;

  useEffect(() => {
    if (status === 'idle' || status === 'arrived') {
      capturedKeyRef.current = null;
      return;
    }
    if (
      (status === 'engaged' || status === 'paused')
      && courseKey
      && capturedKeyRef.current !== courseKey
      && currentSector
    ) {
      capturedKeyRef.current = courseKey;
      setOrigin({ sector_id: currentSector.sector_id, name: currentSector.name });
    }
  }, [status, courseKey, currentSector]);

  // Visible only while actively flying. Disappears on arrival (or idle).
  if (!course || course.hops.length === 0) return null;
  if (status !== 'engaged' && status !== 'paused') return null;

  const chain = buildAutopilotChain(origin, course.hops, currentHopIndex);
  const dest = course.hops[course.hops.length - 1];
  const paused = status === 'paused';

  return (
    <div
      className={`autopilot-hud${paused ? ' autopilot-hud--paused' : ''}`}
      role="status"
      aria-live="polite"
      aria-label="ARIA Autopilot course"
    >
      <div className="autopilot-hud__head">
        <span className="autopilot-hud__badge">ARIA AUTOPILOT</span>
        <span className="autopilot-hud__dest">
          → {dest?.name ?? `Sector ${course.target_sector_id}`}
        </span>
        <span className="autopilot-hud__count">
          {Math.min(currentHopIndex + 1, course.hops.length)}/{course.hops.length}
        </span>
        {paused && <span className="autopilot-hud__paused-tag">HOLD</span>}
      </div>

      <ol className="autopilot-hud__chain">
        {chain.map((node, i) => (
          <li
            key={`${node.sector_id}-${i}`}
            className={`autopilot-hud__node is-${node.state}`}
          >
            {node.legState && (
              <span
                className={`autopilot-hud__leg is-${node.legState}`}
                aria-hidden="true"
              />
            )}
            <span className="autopilot-hud__dot" aria-hidden="true" />
            <span className="autopilot-hud__name">{node.name}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
