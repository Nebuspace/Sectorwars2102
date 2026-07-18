/**
 * warpCinematicBus — fire the windshield warp cinematic from anywhere
 * that commits a sector jump (manual helm or ARIA autopilot).
 *
 * GameDashboard arms `warpDepart` on the WindshieldTableau; AutopilotContext
 * must request a depart *before* each moveToSector so charge/launch/arrive
 * play. Manual handleMove can keep setting state directly or also use this.
 *
 * Timing (single source of truth — WindshieldTableau CSS/JS must match):
 *   TURN → CHARGE → LAUNCH → ARRIVE
 * The hull re-orients with RCS jets during TURN; the warp field does not
 * inflate until TURN ends. Callers MUST delay moveToSector by WARP_TURN_MS
 * so the sector swap cannot abort the turn.
 */

/** Hull re-orients toward exit bearing (RCS jets). No warp bubble yet. */
export const WARP_TURN_MS = 3000;
/** Warp field inflates after the turn. */
export const WARP_MIN_CHARGE_MS = 3200;
/** Warp-away streak. */
export const WARP_LAUNCH_MS = 1700;
/** Bubble-in / arrival flash. */
export const WARP_ARRIVE_MS = 3400;
/** Abandon a stuck buildup (turn + charge + slack). */
export const WARP_CHARGE_TIMEOUT_MS = 11000;

export interface WarpDepartRequest {
  destinationSectorId: number;
  requestId: number;
}

let nextRequestId = 1;
const listeners = new Set<(request: WarpDepartRequest) => void>();

/** Project a real 3D sector vector onto the windshield's 2D plane. */
export function projectedWarpBearing(
  from: { x: number; y: number },
  to: { x: number; y: number },
  fallbackDeg: number,
): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.hypot(dx, dy) < 1e-6) return fallbackDeg;
  return (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
}

/**
 * Give each arrival a direction that is visibly different from departure.
 * The request token varies every hop; the 95–264° offset can never collapse
 * back onto the departure bearing.
 */
export function arrivalBearingForWarp(departureDeg: number, token: number): number {
  const offset = 95 + ((token * 73) % 170);
  return (departureDeg + offset) % 360;
}

/** Ask the cockpit to play the leave/arrive warp cinematic for this hop. */
export function requestWarpDepart(destinationSectorId: number): void {
  const request: WarpDepartRequest = {
    destinationSectorId,
    requestId: nextRequestId++,
  };
  listeners.forEach((fn) => fn(request));
}

export function subscribeWarpDepart(
  fn: (request: WarpDepartRequest) => void,
): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Test-only reset. */
export function __resetWarpCinematicBusForTests(): void {
  nextRequestId = 1;
  listeners.clear();
}
