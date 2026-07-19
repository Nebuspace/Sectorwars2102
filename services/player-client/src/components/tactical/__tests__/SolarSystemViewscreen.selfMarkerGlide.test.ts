// @vitest-environment jsdom
/**
 * WO-SOLAR-MOVEMENT regression guard.
 *
 * Max's live-driving report on the SolarSystemViewscreen top-down tableau:
 * right-click Travel-Here reorients correctly, then the marker BLINKS to the
 * destination, sits, then "reorients backwards, fires its jet in place."
 *
 * Root cause (confirmed by trace, SolarSystemViewscreen.tsx): the draw loop
 * held the marker's heading at the leg's constant direction WHILE gliding,
 * but the instant the glide finished it fell back to shipPos()'s wall-clock
 * idle-drift angle — a value with no relation to the leg just flown. That
 * produced a heading SNAP at arrival (the "reorients backwards" bug); the
 * marker's exhaust flame (always rendered for the self marker) then reads as
 * firing in the wrong direction while stationary ("in place").
 *
 * The fix (resolveSelfMarker, exported) mirrors the NPC dockCycle's CRUISE→
 * DWELL discipline: ease position via smoothstep-over-clock while traveling,
 * then FREEZE both position and heading at the parked values — never a
 * revert to the idle-drift angle. This suite exercises the exported pure
 * function directly (no canvas/RAF harness needed, per the WO's own
 * guidance) and proves:
 *   1. Position progresses MONOTONICALLY toward the destination across the
 *      whole leg (no single-frame jump from ~from to ~to = no "blink").
 *   2. Heading is held at the constant leg direction throughout the glide
 *      (the "reorientation" phase Max confirmed already works).
 *   3. On arrival, heading freezes at that same direction and stays there
 *      for arbitrarily later timestamps (no in-place retrograde flip).
 */
import { describe, it, expect } from 'vitest';
import { resolveSelfMarker, type SelfTravel } from '../SolarSystemViewscreen';

const DUR_MS = 900;

describe('resolveSelfMarker — WO-SOLAR-MOVEMENT', () => {
  const from = { x: 100, y: 100 };
  const to = { x: 500, y: 300 };
  const startMs = 1_000_000;
  const legAngle = Math.atan2(to.y - from.y, to.x - from.x);

  it('progresses monotonically toward the destination across the leg — no blink', () => {
    const travel: SelfTravel = { from, to, startMs };
    const N = 20;
    const samples = Array.from({ length: N + 1 }, (_, i) =>
      resolveSelfMarker(travel, from, null, DUR_MS, startMs + (DUR_MS * i) / N)
    );

    // First sample (t0) is at the origin; last (t0+dur) is exactly the
    // destination — the leg's full span is actually covered, not skipped.
    expect(samples[0].base.x).toBeCloseTo(from.x, 5);
    expect(samples[0].base.y).toBeCloseTo(from.y, 5);
    expect(samples[N].base.x).toBeCloseTo(to.x, 5);
    expect(samples[N].base.y).toBeCloseTo(to.y, 5);

    // Monotonic progress: each sample's distance-remaining-to-destination is
    // strictly non-increasing, and no single step covers more than ~20% of
    // the total distance (a genuine glide, not a teleport mid-leg).
    const totalDist = Math.hypot(to.x - from.x, to.y - from.y);
    const distTo = (p: { x: number; y: number }) => Math.hypot(to.x - p.x, to.y - p.y);
    for (let i = 1; i <= N; i++) {
      expect(distTo(samples[i].base)).toBeLessThanOrEqual(distTo(samples[i - 1].base) + 1e-9);
      const step = Math.hypot(
        samples[i].base.x - samples[i - 1].base.x,
        samples[i].base.y - samples[i - 1].base.y
      );
      expect(step).toBeLessThan(totalDist * 0.2);
    }
  });

  it('holds heading at the constant leg direction throughout the glide (reorientation phase, unaffected)', () => {
    const travel: SelfTravel = { from, to, startMs };
    for (const frac of [0, 0.1, 0.3, 0.5, 0.75, 0.99]) {
      const r = resolveSelfMarker(travel, from, null, DUR_MS, startMs + DUR_MS * frac);
      expect(r.heading).toBeCloseTo(legAngle, 10);
      expect(r.justParked).toBe(false);
    }
  });

  it('freezes at the parked heading on arrival — no revert to an ambient idle-drift angle', () => {
    const travel: SelfTravel = { from, to, startMs };
    const arrival = resolveSelfMarker(travel, from, null, DUR_MS, startMs + DUR_MS);
    expect(arrival.justParked).toBe(true);
    expect(arrival.base).toEqual(to);
    expect(arrival.heading).toBeCloseTo(legAngle, 10);

    // Caller commits arrival.base/heading as the new parked state (mirrors
    // the draw loop's justParked branch), then travel goes null. Heading
    // must stay pinned at legAngle for ANY later wall-clock moment — the
    // bug was a `now`-dependent angle sneaking back in here.
    for (const laterNow of [
      startMs + DUR_MS, startMs + DUR_MS + 1, startMs + DUR_MS + 3719, startMs + 10_000_000
    ]) {
      const parked = resolveSelfMarker(null, arrival.base, arrival.heading, DUR_MS, laterNow);
      expect(parked.justParked).toBe(false);
      expect(parked.base).toEqual(to);
      expect(parked.heading).toBeCloseTo(legAngle, 10);

      // Explicitly rule out the reported symptom: heading must not have
      // flipped ~180° (a retrograde snap) from the arrival direction.
      const flipped = legAngle + Math.PI;
      const normalizedDelta = Math.atan2(Math.sin(parked.heading! - flipped), Math.cos(parked.heading! - flipped));
      expect(Math.abs(normalizedDelta)).toBeGreaterThan(0.5); // nowhere near the retrograde angle
    }
  });

  it('before any Travel-Here, falls back to the idle-drift angle (null heading) — unchanged pre-fix behavior', () => {
    const resting = resolveSelfMarker(null, from, null, DUR_MS, startMs);
    expect(resting.heading).toBeNull();
    expect(resting.base).toEqual(from);
    expect(resting.justParked).toBe(false);
  });

  it('a second Travel-Here started mid-glide resumes from the CURRENT eased point, not the original start', () => {
    // Mirrors travelMarkerTo's resume behavior: the caller re-derives `from`
    // via easeToward before building the next SelfTravel. Confirms
    // resolveSelfMarker treats that resumed leg identically to a fresh one
    // (no special-casing that could reintroduce a jump).
    const firstLeg: SelfTravel = { from, to, startMs };
    const midway = resolveSelfMarker(firstLeg, from, null, DUR_MS, startMs + DUR_MS * 0.4);
    const secondTo = { x: 50, y: 400 };
    const secondLeg: SelfTravel = { from: midway.base, to: secondTo, startMs: startMs + DUR_MS * 0.4 };

    const justAfterRedirect = resolveSelfMarker(secondLeg, midway.base, midway.heading, DUR_MS, startMs + DUR_MS * 0.4 + 1);
    // No jump: the very next frame after redirecting starts essentially at
    // the point the marker was already at.
    expect(Math.hypot(
      justAfterRedirect.base.x - midway.base.x, justAfterRedirect.base.y - midway.base.y
    )).toBeLessThan(1);
  });
});
