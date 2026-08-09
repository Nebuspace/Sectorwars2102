/**
 * intrasystemFlight — phase class map + deriveIspPose timeline.
 */
import { describe, expect, it } from 'vitest';
import {
  ISP_ACCEL_MS,
  ISP_COAST_MS,
  ISP_FLIP_MS,
  ISP_HALT_BRAKE_MS,
  ISP_HALT_FLIP_MS,
  ISP_MOVE_MS,
  ISP_ORIENT_MS,
  ISP_SETTLE_MS,
  deriveIspPose,
  ispPhaseToTravelClass,
  parseIspTime,
  type IspPose,
} from '../intrasystemFlight';

const t0 = Date.parse('2026-01-01T00:00:00.000Z');

const burnPose = (elapsedMs: number): IspPose => ({
  x_pct: 10,
  y_pct: 20,
  heading_deg: 0,
  phase: 'orienting',
  burning: false,
  leg: {
    kind: 'burn',
    from_x: 10,
    from_y: 20,
    to_x: 90,
    to_y: 80,
    started_at: new Date(t0).toISOString(),
    prograde_deg: 45,
    parked_heading_deg: 0,
  },
  server_time: new Date(t0 + elapsedMs).toISOString(),
});

describe('intrasystemFlight', () => {
  it('maps special phases onto travel CSS class names', () => {
    expect(ispPhaseToTravelClass('brake_turn')).toBe('brake-turn');
    expect(ispPhaseToTravelClass('halt_turn')).toBe('halt-turn');
    expect(ispPhaseToTravelClass('halt_brake')).toBe('halt-brake');
    expect(ispPhaseToTravelClass('final_orient')).toBe('final-orient');
    expect(ispPhaseToTravelClass('accelerating')).toBe('accelerating');
  });

  it('parseIspTime falls back to now for bad input', () => {
    expect(parseIspTime('2026-01-01T00:00:00.000Z')).toBe(t0);
    const before = Date.now();
    const parsed = parseIspTime('not-a-date');
    expect(parsed).toBeGreaterThanOrEqual(before);
  });

  it('returns idle defaults for null pose and legsless pose', () => {
    expect(deriveIspPose(null).phase).toBe('idle');
    expect(deriveIspPose({ x_pct: 1, y_pct: 2, heading_deg: 3, phase: 'x', burning: true }).phase).toBe(
      'idle',
    );
  });

  it('walks burn phases: orient → accel → glide → flip → brake → settle → idle', () => {
    expect(deriveIspPose(burnPose(0), t0).phase).toBe('orienting');
    expect(deriveIspPose(burnPose(ISP_ORIENT_MS + 1), t0 + ISP_ORIENT_MS + 1).phase).toBe(
      'accelerating',
    );
    expect(
      deriveIspPose(burnPose(ISP_ORIENT_MS + ISP_ACCEL_MS + 1), t0 + ISP_ORIENT_MS + ISP_ACCEL_MS + 1)
        .phase,
    ).toBe('gliding');
    expect(
      deriveIspPose(
        burnPose(ISP_ORIENT_MS + ISP_ACCEL_MS + ISP_COAST_MS + 1),
        t0 + ISP_ORIENT_MS + ISP_ACCEL_MS + ISP_COAST_MS + 1,
      ).phase,
    ).toBe('brake_turn');
    expect(
      deriveIspPose(
        burnPose(ISP_ORIENT_MS + ISP_ACCEL_MS + ISP_COAST_MS + ISP_FLIP_MS + 1),
        t0 + ISP_ORIENT_MS + ISP_ACCEL_MS + ISP_COAST_MS + ISP_FLIP_MS + 1,
      ).phase,
    ).toBe('braking');
    expect(
      deriveIspPose(burnPose(ISP_ORIENT_MS + ISP_MOVE_MS + 1), t0 + ISP_ORIENT_MS + ISP_MOVE_MS + 1)
        .phase,
    ).toBe('final_orient');
    expect(
      deriveIspPose(
        burnPose(ISP_ORIENT_MS + ISP_MOVE_MS + ISP_SETTLE_MS + 1),
        t0 + ISP_ORIENT_MS + ISP_MOVE_MS + ISP_SETTLE_MS + 1,
      ).phase,
    ).toBe('idle');
  });

  it('handles halt legs: flip then brake then idle at destination', () => {
    const haltAt = (elapsed: number): IspPose => ({
      x_pct: 0,
      y_pct: 0,
      heading_deg: 0,
      phase: 'halt_turn',
      burning: false,
      leg: {
        kind: 'halt',
        from_x: 0,
        from_y: 0,
        to_x: 50,
        to_y: 50,
        started_at: new Date(t0).toISOString(),
        prograde_deg: 90,
      },
    });

    expect(deriveIspPose(haltAt(0), t0).phase).toBe('halt_turn');
    expect(deriveIspPose(haltAt(0), t0).burning).toBe(false);
    expect(deriveIspPose(haltAt(0), t0 + ISP_HALT_FLIP_MS + 1).phase).toBe('halt_brake');
    expect(deriveIspPose(haltAt(0), t0 + ISP_HALT_FLIP_MS + 1).burning).toBe(true);
    const done = deriveIspPose(haltAt(0), t0 + ISP_HALT_FLIP_MS + ISP_HALT_BRAKE_MS + 1);
    expect(done.phase).toBe('idle');
    expect(done.x_pct).toBe(50);
    expect(done.y_pct).toBe(50);
    expect(done.leg).toBeNull();
  });
});
