import { describe, it, expect, beforeEach } from 'vitest';
import {
  arrivalBearingForWarp,
  projectedWarpBearing,
  requestWarpDepart,
  subscribeWarpDepart,
  WARP_TURN_MS,
  __resetWarpCinematicBusForTests,
} from '../warpCinematicBus';

describe('warpCinematicBus', () => {
  beforeEach(() => {
    __resetWarpCinematicBusForTests();
  });

  it('exports a multi-second turn delay so callers can hold the hop', () => {
    expect(WARP_TURN_MS).toBeGreaterThanOrEqual(2500);
  });

  it('delivers destination sector ids to subscribers', () => {
    const seen: number[] = [];
    const unsub = subscribeWarpDepart((req) => {
      seen.push(req.destinationSectorId);
    });
    requestWarpDepart(19);
    requestWarpDepart(6);
    unsub();
    requestWarpDepart(1);
    expect(seen).toEqual([19, 6]);
  });

  it('increments requestId per fire', () => {
    const ids: number[] = [];
    subscribeWarpDepart((req) => ids.push(req.requestId));
    requestWarpDepart(10);
    requestWarpDepart(11);
    expect(ids[1]).toBeGreaterThan(ids[0]);
  });

  it('projects real sector coordinates onto the 2D windshield', () => {
    expect(projectedWarpBearing({ x: 10, y: 10 }, { x: 20, y: 10 }, 123)).toBe(0);
    expect(projectedWarpBearing({ x: 10, y: 10 }, { x: 10, y: 20 }, 123)).toBe(90);
    expect(projectedWarpBearing({ x: 10, y: 10 }, { x: 10, y: 10 }, 123)).toBe(123);
  });

  it('gives every arrival a materially different angle from departure', () => {
    const departure = 42;
    const first = arrivalBearingForWarp(departure, 1);
    const second = arrivalBearingForWarp(departure, 2);
    const circularDifference = (a: number, b: number) => {
      const raw = Math.abs(a - b) % 360;
      return Math.min(raw, 360 - raw);
    };
    expect(circularDifference(first, departure)).toBeGreaterThanOrEqual(95);
    expect(circularDifference(second, departure)).toBeGreaterThanOrEqual(95);
    expect(second).not.toBe(first);
  });
});
