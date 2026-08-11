/**
 * windshieldTableauHelpers — pure, DOM/hook-free geometry + travel-phase
 * helpers (WO-AAA-SOLAR-TABLEAU phase 3 module split). Its own header
 * documents these as extracted VERBATIM from WindshieldTableau.tsx's module
 * scope. `chooseWarpArrivalAnchor` already has its own describe block in
 * WindshieldTableau.test.tsx and `resolveShipPose`/`orbitEllipse` are only
 * exercised indirectly through full-component rendering elsewhere -- this
 * file covers the rest of the module's exports directly, which had no
 * dedicated coverage at all: shortestAngleDelta, clampPct,
 * redirectArcWaypoint, isInFlightPhase, distancePx, stationApproachPoint,
 * toStaticSystem, arcPath, and resolveShipPose's three pose-resolution
 * branches (server-tracked pose / cosmetic NPC wander / poseless-human
 * parked fallback).
 */
import { describe, it, expect } from 'vitest';
import {
  shortestAngleDelta,
  clampPct,
  redirectArcWaypoint,
  isInFlightPhase,
  distancePx,
  stationApproachPoint,
  toStaticSystem,
  arcPath,
  resolveShipPose,
  type TravelPhase,
} from '../windshieldTableauHelpers';
import type { BandGeometry, StarAnchor, HazardArc } from '../windshieldTableauLayout';
import type { ShipPresence } from '../SolarSystemViewscreen';

const BAND: BandGeometry = { widthPx: 1440, heightPx: 334.7, remPx: 18.09 };

describe('shortestAngleDelta', () => {
  it('returns a small positive delta for a straightforward CW turn', () => {
    expect(shortestAngleDelta(0, 90)).toBe(90);
  });

  it('returns a small positive delta when wrapping past 360', () => {
    expect(shortestAngleDelta(350, 10)).toBe(20);
  });

  it('returns a small negative delta when wrapping the other way', () => {
    expect(shortestAngleDelta(10, 350)).toBe(-20);
  });

  it('resolves an exact 180 to -180 (the formula falls on the lower edge here)', () => {
    expect(shortestAngleDelta(0, 180)).toBe(-180);
  });

  it('returns 0 for no change', () => {
    expect(shortestAngleDelta(45, 45)).toBe(0);
  });
});

describe('clampPct', () => {
  it('passes through in-range values unchanged', () => {
    expect(clampPct(50)).toBe(50);
  });

  it('clamps below 2 up to 2', () => {
    expect(clampPct(-10)).toBe(2);
  });

  it('clamps above 98 down to 98', () => {
    expect(clampPct(150)).toBe(98);
  });
});

describe('redirectArcWaypoint', () => {
  it('blends a coast-then-bend point toward the target, clamped to [2,98]', () => {
    const result = redirectArcWaypoint(
      { xPct: 50, yPct: 50 },
      { x: 1, y: 0 },
      { xPct: 60, yPct: 50 },
    );
    // toTarget=10, lead=clamp(10*0.32,4,14)=4  -> coast x=54
    // blended x = 54 + (60-54)*0.4 = 56.4
    expect(result.xPct).toBeCloseTo(56.4, 5);
    expect(result.yPct).toBeCloseTo(50, 5);
  });

  it('clamps the lead distance to a max of 14 for a far target', () => {
    const result = redirectArcWaypoint(
      { xPct: 10, yPct: 50 },
      { x: 1, y: 0 },
      { xPct: 90, yPct: 50 },
    );
    // toTarget=80, lead=clamp(80*0.32,4,14)=14 -> coast x=24; blend = 24+(90-24)*0.4=50.4
    expect(result.xPct).toBeCloseTo(50.4, 5);
  });

  it('clamps the resulting point into the [2,98] band', () => {
    const result = redirectArcWaypoint(
      { xPct: 99, yPct: 99 },
      { x: 1, y: 1 },
      { xPct: 99, yPct: 99 },
    );
    expect(result.xPct).toBeLessThanOrEqual(98);
    expect(result.yPct).toBeLessThanOrEqual(98);
  });
});

describe('isInFlightPhase', () => {
  const inFlight: TravelPhase[] = ['accelerating', 'gliding', 'brake-turn', 'braking', 'halt-turn', 'halt-brake', 'redirect-turn'];
  const notInFlight: TravelPhase[] = ['idle', 'orienting', 'final-orient'];

  it.each(inFlight)('treats %s as in-flight', (phase) => {
    expect(isInFlightPhase(phase)).toBe(true);
  });

  it.each(notInFlight)('treats %s as not in-flight', (phase) => {
    expect(isInFlightPhase(phase)).toBe(false);
  });
});

describe('distancePx', () => {
  it('converts a %-space delta into real pixels via the band aspect', () => {
    // dx = 10% of 1440 = 144px, dy = 0 -> hypot = 144
    expect(distancePx({ xPct: 0, yPct: 0 }, { xPct: 10, yPct: 0 }, BAND)).toBeCloseTo(144, 5);
  });

  it('is symmetric and zero for identical points', () => {
    const p = { xPct: 33, yPct: 67 };
    expect(distancePx(p, p, BAND)).toBe(0);
  });
});

describe('stationApproachPoint', () => {
  it('stops short of the station along the approach vector by the standoff distance', () => {
    // from directly left of station: dx=100%->width px, dy=0
    const from = { xPct: 0, yPct: 50 };
    const station = { xPct: 50, yPct: 50 };
    const result = stationApproachPoint(from, station, BAND);
    // Approach point should be strictly between from and station (closer to station than "from" is)
    expect(result.xPct).toBeGreaterThan(from.xPct);
    expect(result.xPct).toBeLessThan(station.xPct);
    expect(result.yPct).toBeCloseTo(50, 1);
  });

  it('defaults to a rightward unit vector when from and station coincide (degenerate zero-length case)', () => {
    const point = { xPct: 40, yPct: 40 };
    const result = stationApproachPoint(point, point, BAND);
    // ux=1, uy=0 fallback -> result should shift in +x only, clamped to [3,97]
    expect(result.xPct).toBeGreaterThan(point.xPct);
    expect(result.yPct).toBeCloseTo(point.yPct, 1);
  });

  it('clamps the result within the [3,97] x [6,94] safe band', () => {
    const from = { xPct: 1, yPct: 1 };
    const station = { xPct: 0, yPct: 0 };
    const result = stationApproachPoint(from, station, BAND);
    expect(result.xPct).toBeGreaterThanOrEqual(3);
    expect(result.xPct).toBeLessThanOrEqual(97);
    expect(result.yPct).toBeGreaterThanOrEqual(6);
    expect(result.yPct).toBeLessThanOrEqual(94);
  });
});

describe('toStaticSystem', () => {
  it('defaults every field defensively for null/undefined input', () => {
    const result = toStaticSystem(null);
    expect(result).toEqual({
      star: null, nebula: null, belt: null, debris: null,
      bodies: [], stations: [], messageBeacons: [],
    });
  });

  it('passes through provided scalar fields and coerces non-array bodies/stations to []', () => {
    const result = toStaticSystem({
      star: { kind: 'g', label: 'Sol', color: '#fff' },
      bodies: 'not-an-array',
      stations: null,
      message_beacons: [{ id: 'b1', deployer_nickname: 'x', deployed_at: null, preview: 'hi', expiry: null }],
    });
    expect(result.star).toEqual({ kind: 'g', label: 'Sol', color: '#fff' });
    expect(result.bodies).toEqual([]);
    expect(result.stations).toEqual([]);
    expect(result.messageBeacons).toHaveLength(1);
  });

  it('maps the snake_case message_beacons field to messageBeacons', () => {
    const beacons = [{ id: 'b1', deployer_nickname: 'x', deployed_at: null, preview: 'hi', expiry: null }];
    const result = toStaticSystem({ message_beacons: beacons });
    expect(result.messageBeacons).toBe(beacons);
  });
});

describe('arcPath', () => {
  it('builds an SVG arc path string from a star anchor and hazard arc', () => {
    const star: StarAnchor = { xPct: 50, yPct: 50, sizeEm: 3 };
    const arc: HazardArc = { rFrac: 0.5, startDeg: 0, sweepDeg: 90 };
    const path = arcPath(star, arc);
    expect(path).toMatch(/^M [\d.-]+ [\d.-]+ A [\d.-]+ [\d.-]+ 0 0 1 [\d.-]+ [\d.-]+$/);
  });

  it('sets the large-arc-flag to 1 when the sweep exceeds 180deg', () => {
    const star: StarAnchor = { xPct: 50, yPct: 50, sizeEm: 3 };
    const arc: HazardArc = { rFrac: 0.5, startDeg: 0, sweepDeg: 270 };
    const path = arcPath(star, arc);
    expect(path).toContain(' 1 1 ');
  });
});

describe('resolveShipPose', () => {
  const contactDocks: never[] = [];

  it('resolves via the server-tracked pose (deriveIspPose) when s.pose is present', () => {
    const s: ShipPresence = {
      ship_id: 'ship-1',
      pose: { x_pct: 42, y_pct: 17, heading_deg: 90, phase: 'gliding', burning: true },
    };
    const result = resolveShipPose(s, 1_000_000, 0, contactDocks, 1);
    // No leg on this pose -> deriveIspPose's legless branch: parked at the
    // pose's own x/y with phase forced to idle (burning always false there).
    expect(result.xPct).toBe(42);
    expect(result.yPct).toBe(17);
    expect(result.burning).toBe(false);
    expect(result.phaseClass).toBe('idle');
  });

  it('resolves via the cosmetic NPC wander when is_npc is true and no pose exists', () => {
    const s: ShipPresence = {
      ship_id: 'ship-2',
      is_npc: true,
      archetype: 'TRADER',
      activity: 'COMMUTE',
      mission: 'commerce',
    };
    const result = resolveShipPose(s, 0, 0, contactDocks, 1);
    expect(Number.isFinite(result.xPct)).toBe(true);
    expect(Number.isFinite(result.yPct)).toBe(true);
    expect(typeof result.phaseClass).toBe('string');
  });

  it('parks a poseless human contact at a stable, deterministic anchor (FIX-POSELESS-FALLBACK)', () => {
    const s: ShipPresence = { ship_id: 'ship-3', player_id: 'player-abc', is_npc: false };
    const a = resolveShipPose(s, 0, 0, contactDocks, 1);
    const b = resolveShipPose(s, 999_999, 5, contactDocks, 1);
    // Identical inputs -> identical anchor regardless of nowMs/contactT (no time-driven wander).
    expect(a).toEqual(b);
    expect(a.headingDeg).toBe(0);
    expect(a.burning).toBe(false);
    expect(a.phaseClass).toBe('idle');
  });
});
