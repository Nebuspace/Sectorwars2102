/**
 * Shared intra-system flight interpolator — lockstep with
 * gameserver `intrasystem_movement_service.py` (WO-ISP).
 *
 * Clients animate from a server leg plan + server_time; they do not invent
 * independent clocks for authoritative contacts.
 */
export const ISP_ORIENT_MS = 1000;
export const ISP_ACCEL_MS = 1800;
export const ISP_COAST_MS = 1100;
export const ISP_FLIP_MS = 1300;
export const ISP_DECEL_MS = 2200;
export const ISP_SETTLE_MS = 800;
export const ISP_MOVE_MS = ISP_ACCEL_MS + ISP_COAST_MS + ISP_FLIP_MS + ISP_DECEL_MS;
export const ISP_HALT_FLIP_MS = 1800;
export const ISP_HALT_BRAKE_MS = 1600;

export const ISP_REF_BAND_ASPECT = 335 / 1440;

export type IspPhase =
  | 'idle'
  | 'orienting'
  | 'accelerating'
  | 'gliding'
  | 'brake_turn'
  | 'braking'
  | 'final_orient'
  | 'halt_turn'
  | 'halt_brake';

export interface IspLeg {
  kind?: string;
  from_x: number;
  from_y: number;
  to_x: number;
  to_y: number;
  started_at: string;
  prograde_deg?: number;
  parked_heading_deg?: number;
  target_kind?: string | null;
  target_id?: string | null;
}

export interface IspPose {
  x_pct: number;
  y_pct: number;
  heading_deg: number;
  phase: IspPhase | string;
  burning: boolean;
  leg?: IspLeg | null;
  server_time?: string;
}

function smoothstep(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
}

function shortestDelta(from: number, to: number): number {
  return ((to - from + 540) % 360) - 180;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function parseIspTime(iso?: string | null): number {
  if (!iso) return Date.now();
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : Date.now();
}

/** Map server phase names onto windshield travel-* CSS classes. */
export function ispPhaseToTravelClass(phase: string): string {
  if (phase === 'brake_turn') return 'brake-turn';
  if (phase === 'halt_turn') return 'halt-turn';
  if (phase === 'halt_brake') return 'halt-brake';
  if (phase === 'final_orient') return 'final-orient';
  return phase;
}

export function deriveIspPose(pose: IspPose | null | undefined, nowMs: number = Date.now()): IspPose {
  if (!pose) {
    return { x_pct: 50, y_pct: 50, heading_deg: 0, phase: 'idle', burning: false, leg: null };
  }
  const leg = pose.leg;
  if (!leg?.started_at) {
    return {
      x_pct: pose.x_pct,
      y_pct: pose.y_pct,
      heading_deg: pose.heading_deg,
      phase: 'idle',
      burning: false,
      leg: null,
    };
  }

  const started = parseIspTime(leg.started_at);
  const elapsed = Math.max(0, nowMs - started);
  const kind = (leg.kind || 'burn').toLowerCase();
  const fx = leg.from_x;
  const fy = leg.from_y;
  const tx = leg.to_x;
  const ty = leg.to_y;
  const prograde = leg.prograde_deg ?? 0;
  const parked = leg.parked_heading_deg ?? pose.heading_deg ?? prograde;

  if (kind === 'halt') {
    const total = ISP_HALT_FLIP_MS + ISP_HALT_BRAKE_MS;
    const retrograde = prograde + 180;
    if (elapsed < ISP_HALT_FLIP_MS) {
      const t = smoothstep(elapsed / ISP_HALT_FLIP_MS);
      const p = 0.38 * t;
      return {
        x_pct: lerp(fx, tx, p),
        y_pct: lerp(fy, ty, p),
        heading_deg: prograde + 180 * t,
        phase: 'halt_turn',
        burning: false,
        leg,
      };
    }
    if (elapsed < total) {
      const t = smoothstep((elapsed - ISP_HALT_FLIP_MS) / ISP_HALT_BRAKE_MS);
      const p = 0.38 + 0.62 * t;
      return {
        x_pct: lerp(fx, tx, p),
        y_pct: lerp(fy, ty, p),
        heading_deg: retrograde,
        phase: 'halt_brake',
        burning: true,
        leg,
      };
    }
    return { x_pct: tx, y_pct: ty, heading_deg: retrograde, phase: 'idle', burning: false, leg: null };
  }

  const retrograde = prograde + 180;
  const face = prograde + 360;
  const moveStart = ISP_ORIENT_MS;
  const accelEnd = moveStart + ISP_ACCEL_MS;
  const coastEnd = accelEnd + ISP_COAST_MS;
  const flipEnd = coastEnd + ISP_FLIP_MS;
  const moveEnd = moveStart + ISP_MOVE_MS;
  const settleEnd = moveEnd + ISP_SETTLE_MS;

  if (elapsed < moveStart) {
    const t = smoothstep(elapsed / ISP_ORIENT_MS);
    return {
      x_pct: fx,
      y_pct: fy,
      heading_deg: parked + shortestDelta(parked, prograde) * t,
      phase: 'orienting',
      burning: false,
      leg,
    };
  }
  if (elapsed < moveEnd) {
    const p = smoothstep((elapsed - moveStart) / ISP_MOVE_MS);
    const x = lerp(fx, tx, p);
    const y = lerp(fy, ty, p);
    if (elapsed < accelEnd) {
      return { x_pct: x, y_pct: y, heading_deg: prograde, phase: 'accelerating', burning: true, leg };
    }
    if (elapsed < coastEnd) {
      return { x_pct: x, y_pct: y, heading_deg: prograde, phase: 'gliding', burning: false, leg };
    }
    if (elapsed < flipEnd) {
      const ft = smoothstep((elapsed - coastEnd) / ISP_FLIP_MS);
      return { x_pct: x, y_pct: y, heading_deg: prograde + 180 * ft, phase: 'brake_turn', burning: false, leg };
    }
    return { x_pct: x, y_pct: y, heading_deg: retrograde, phase: 'braking', burning: true, leg };
  }
  if (elapsed < settleEnd) {
    const t = smoothstep((elapsed - moveEnd) / ISP_SETTLE_MS);
    return {
      x_pct: tx,
      y_pct: ty,
      heading_deg: retrograde + (face - retrograde) * t,
      phase: 'final_orient',
      burning: false,
      leg,
    };
  }
  return { x_pct: tx, y_pct: ty, heading_deg: face, phase: 'idle', burning: false, leg: null };
}
