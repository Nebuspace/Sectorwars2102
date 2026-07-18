/**
 * drawPlanetTableau — t-DEPENDENCE DIAGNOSTIC (not a feature test; changes no
 * source). Answers one question: is `drawPlanetTableau`'s op-log a function
 * of `t`, per treatment kind, in JS DOUBLE PRECISION? That's it — this file
 * verifies the draw FUNCTION's arguments (rotate() angles, alpha, particle
 * coordinates) are mathematically t-dependent when computed as ordinary JS
 * doubles. It does NOT reflect what actually paints on screen.
 *
 * CAVEAT (load-bearing — read before trusting a green run here): an earlier
 * version of this file had a "PASS 2" CTM-transformed raster-geometry block
 * that additionally computed the rotation matrix's cos/sin in JS double
 * precision and concluded the painted pixels genuinely move — a FALSE
 * NEGATIVE. The real bug (`drawPlanetSurfaceTableau`'s `spin` feeding a huge
 * raw-epoch `t` into `ctx.rotate()`) froze on screen because Chromium's Skia
 * Canvas2D backend represents its transform-matrix trig in float32
 * internally, not because the JS-level math was wrong — a double-precision
 * CTM simulation cannot see that collapse, so it "proved" motion that wasn't
 * there. That block has been removed rather than kept-with-a-caveat, since
 * its whole premise (double-precision geometry ⇒ real on-screen motion) is
 * the exact misread this bug exploited. The real-Chromium regression guard
 * for this bug class lives at `playwright/e2e/tableau-freeze-repro.spec.ts`
 * (`getImageData` hash diff on an actual rendered canvas) — that is the ONLY
 * test in this codebase that can actually catch a Skia/GLSL float32 freeze;
 * this file cannot and should not be read as proof against one.
 *
 * Ground truth already established (see the dispatch brief): the harness
 * feeds every registration `t = Date.now()/1000` and the planet + star share
 * the SAME `t`/rAF, so if this file finds every non-forming treatment's
 * op-log DIFFERS across `t`, that's necessary-but-not-sufficient evidence
 * against a JS-level t-threading bug (a *different* class of bug than the
 * float32 one above). If any non-forming type comes back IDENTICAL, that's
 * the smoking gun of a real t-threading freeze at the JS level.
 *
 * Mirrors drawPlanetTableau.test.ts's no-op-ctx Proxy convention, extended
 * to RECORD every method call + property set into an ordered op-log instead
 * of discarding it, so two renders at different `t` can be diffed.
 */
import { describe, it, expect } from 'vitest';
import { drawPlanetTableau, type TableauPlanetBody } from '../drawPlanetTableau';
import { pctToPx } from '../tableauFxHarness';
import type { SystemBody } from '../SolarSystemViewscreen';

const W = 1440;
const H = 334.7;
const mapper = (xPct: number, yPct: number) => pctToPx(xPct, yPct, W, H);
const star = { xPct: 12, yPct: 45 };

// Absolute epoch seconds (matches tableauFxHarness's Date.now()/1000 feed),
// +5s and +20s later — per the brief.
const T0 = 1.7526e9;
const T1 = T0 + 5.0;
const T2 = T0 + 20.0;

const SPIN_SCALE = 0.5; // mirrors drawPlanetTableau.tsx's own constant
const CLOUD_DRIFT_RATE = 1.65; // mirrors drawPlanetTableau.tsx's own constant

// ---------------------------------------------------------------------------
// Recording ctx — extends the existing makeNoopCtx shape (real-shaped
// measureText/createRadialGradient/createLinearGradient stand-ins so the
// draw path stays throw-free) but every method call + property set appends
// to an ordered log instead of vanishing into a black hole.
// ---------------------------------------------------------------------------

function round(v: unknown): unknown {
  // 6dp so float noise (e.g. 1e-13 rounding jitter between two otherwise-
  // identical calls) never manufactures a spurious diff.
  if (typeof v === 'number' && Number.isFinite(v)) return Math.round(v * 1e6) / 1e6;
  return v;
}

function makeRecordingCtx(): { ctx: CanvasRenderingContext2D; log: string[] } {
  const log: string[] = [];
  const store: Record<string, unknown> = {};
  const ctx = new Proxy(store, {
    get(target, prop) {
      const name = String(prop);
      if (prop === 'measureText') return () => ({ width: 10 });
      if (prop === 'createRadialGradient' || prop === 'createLinearGradient') {
        return (...args: unknown[]) => {
          log.push(`${name}(${args.map(round).join(',')})`);
          return {
            addColorStop: (...csArgs: unknown[]) => {
              log.push(`addColorStop(${csArgs.map(round).join(',')})`);
            },
          };
        };
      }
      if (prop in target) return target[prop as string];
      return (...args: unknown[]) => {
        log.push(`${name}(${args.map(round).join(',')})`);
      };
    },
    set(target, prop, value) {
      target[prop as string] = value;
      log.push(`${String(prop)}=${round(value)}`);
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, log };
}

function makeBody(kind: string, overrides: Partial<SystemBody> = {}): SystemBody {
  return {
    slot: 0,
    orbit_au: 0.4,
    kind,
    size_class: 2,
    palette: { hue: 140, sat: 55 },
    rings: false,
    moons: 1,
    phase_deg: 30,
    real: true,
    planet_id: 'planet-t-dep',
    name: 'T-Dependence Probe',
    habitability: 62,
    owned: false,
    formation_status: undefined,
    // Fixed (not seed-derived) so deltas below are exactly reproducible and
    // line up with the reference table's rotH=24 row.
    rotation_period_hours: 24,
    axial_tilt_deg: 15,
    ...overrides,
  };
}

function makePlanet(body: SystemBody): TableauPlanetBody {
  return { body, xPct: 40, yPct: 50, rPx: 14 };
}

function renderAt(body: SystemBody, t: number): string[] {
  const { ctx, log } = makeRecordingCtx();
  drawPlanetTableau(ctx, 7, [makePlanet(body)], t, mapper, star);
  return log;
}

/** Pulls every bare `rotate(<n>)` call's numeric arg, in log order. Axial
 *  spin (drawPlanetSurfaceTableau) always fires first; cloud-drift
 *  (drawCloudDrift, TERRAN/OCEANIC only) fires second if present. */
function extractRotates(log: string[]): number[] {
  const out: number[] = [];
  for (const entry of log) {
    const m = /^rotate\(([-\d.eE]+)\)$/.exec(entry);
    if (m) out.push(parseFloat(m[1]));
  }
  return out;
}

const TYPES = ['GAS_GIANT', 'BARREN', 'ICE', 'VOLCANIC', 'DESERT', 'TERRAN', 'OCEANIC'] as const;

type Row = {
  name: string;
  differs01: boolean;
  differs02: boolean;
  axialDeltaRad: number;
  axialDeltaDeg: number;
  cloudDeltaRad?: number;
  cloudDeltaDeg?: number;
};

describe('drawPlanetTableau t-dependence probe (diagnostic — no source changes)', () => {
  const rows: Row[] = [];

  for (const kind of TYPES) {
    it(`${kind}: op-log is a function of t (axial-spin channel)`, () => {
      const body = makeBody(kind);
      const log0 = renderAt(body, T0);
      const log1 = renderAt(body, T1);
      const log2 = renderAt(body, T2);

      const differs01 = JSON.stringify(log0) !== JSON.stringify(log1);
      const differs02 = JSON.stringify(log0) !== JSON.stringify(log2);

      const r0 = extractRotates(log0);
      const r2 = extractRotates(log2);
      const axialDeltaRad = r2[0] - r0[0];
      const axialDeltaDeg = (axialDeltaRad * 180) / Math.PI;

      let cloudDeltaRad: number | undefined;
      let cloudDeltaDeg: number | undefined;
      if (kind === 'TERRAN' || kind === 'OCEANIC') {
        cloudDeltaRad = r2[1] - r0[1];
        cloudDeltaDeg = (cloudDeltaRad * 180) / Math.PI;
      }

      rows.push({ name: kind, differs01, differs02, axialDeltaRad, axialDeltaDeg, cloudDeltaRad, cloudDeltaDeg });

      console.log(
        `[t-dep] ${kind.padEnd(10)} t0-vs-t0+5s=${differs01 ? 'DIFFERS' : 'IDENTICAL'}  ` +
        `t0-vs-t0+20s=${differs02 ? 'DIFFERS' : 'IDENTICAL'}  ` +
        `axialΔ(20s)=${axialDeltaRad.toFixed(6)}rad (${axialDeltaDeg.toFixed(4)}deg)` +
        (cloudDeltaRad !== undefined
          ? `  cloudDriftΔ(20s)=${cloudDeltaRad.toFixed(6)}rad (${cloudDeltaDeg!.toFixed(4)}deg)`
          : '')
      );

      if (!differs01) {
        console.log(`[t-dep] *** SMOKING GUN: ${kind} is IDENTICAL at t0 vs t0+5s — FROZEN ***`);
      }

      expect(differs01, `${kind}: op-log expected to differ across a 5s gap (else FROZEN)`).toBe(true);
      expect(axialDeltaRad, `${kind}: expected nonzero axial rotate() delta over 20s`).not.toBe(0);
    });
  }

  it('rings:true variant (GAS_GIANT+rings) also differs across t', () => {
    const body = makeBody('GAS_GIANT', { rings: true });
    const log0 = renderAt(body, T0);
    const log1 = renderAt(body, T1);
    const differs = JSON.stringify(log0) !== JSON.stringify(log1);
    console.log(`[t-dep] GAS_GIANT+rings t0-vs-t0+5s=${differs ? 'DIFFERS' : 'IDENTICAL'}`);
    expect(differs).toBe(true);
  });

  it('forming variant (TERRAN, formation_status=forming): pulse alpha + particles differ across t', () => {
    const body = makeBody('TERRAN', { formation_status: 'forming' });
    const log0 = renderAt(body, T0);
    const log1 = renderAt(body, T1);
    const differs = JSON.stringify(log0) !== JSON.stringify(log1);

    // Outer dim-and-overlay pulse set directly in drawPlanetTableau:
    // ctx.globalAlpha = 0.35 + 0.15 * Math.sin(t * 1.2)
    const alpha0 = 0.35 + 0.15 * Math.sin(T0 * 1.2);
    const alpha1 = 0.35 + 0.15 * Math.sin(T1 * 1.2);
    const hasAlpha0 = log0.some((e) => e.startsWith('globalAlpha=') && Math.abs(parseFloat(e.split('=')[1]) - alpha0) < 1e-5);
    const hasAlpha1 = log1.some((e) => e.startsWith('globalAlpha=') && Math.abs(parseFloat(e.split('=')[1]) - alpha1) < 1e-5);

    // Particle motes: ang = base + t*speed -> arc(px,py,...) coordinates move.
    const arcs0 = log0.filter((e) => e.startsWith('arc('));
    const arcs1 = log1.filter((e) => e.startsWith('arc('));
    const particlesMoved = JSON.stringify(arcs0) !== JSON.stringify(arcs1);

    console.log(
      `[t-dep] TERRAN+forming t0-vs-t0+5s=${differs ? 'DIFFERS' : 'IDENTICAL'}  ` +
      `pulseAlpha t0=${alpha0.toFixed(6)} t1=${alpha1.toFixed(6)} (found in log: t0=${hasAlpha0} t1=${hasAlpha1})  ` +
      `particleArcsMoved=${particlesMoved}`
    );

    expect(differs).toBe(true);
    expect(hasAlpha0).toBe(true);
    expect(hasAlpha1).toBe(true);
    expect(particlesMoved).toBe(true);
  });

  it('reference table: theoretical axial degrees-over-20s and cloud-drift by rotH', () => {
    console.log('[t-dep] reference table (theoretical, SPIN_SCALE=0.5, 20s window):');
    console.log('  rotH(h) | axial deg/20s | cloudDrift deg/20s (x1.65, TERRAN/OCEANIC only)');
    for (const rotH of [6, 12, 24, 48]) {
      const axialDeg = (20 * SPIN_SCALE * 360) / (rotH * 4);
      const cloudDeg = axialDeg * CLOUD_DRIFT_RATE;
      console.log(`  ${String(rotH).padEnd(7)} | ${axialDeg.toFixed(4).padEnd(13)} | ${cloudDeg.toFixed(4)}`);
    }
    expect(true).toBe(true);
  });

  it('FINAL VERDICT — summary table', () => {
    console.log('');
    console.log('[t-dep] ===================== SUMMARY TABLE =====================');
    console.log('type       | t0 vs +5s | t0 vs +20s | axial Δ20s (rad) | axial Δ20s (deg) | cloud Δ20s (deg)');
    for (const r of rows) {
      console.log(
        `${r.name.padEnd(10)} | ${(r.differs01 ? 'DIFFERS ' : 'IDENTICAL')} | ${(r.differs02 ? 'DIFFERS  ' : 'IDENTICAL ')} | ` +
        `${r.axialDeltaRad.toFixed(6).padEnd(16)} | ${r.axialDeltaDeg.toFixed(4).padEnd(16)} | ` +
        `${r.cloudDeltaDeg !== undefined ? r.cloudDeltaDeg.toFixed(4) : '-'}`
      );
    }
    const anyFrozen = rows.some((r) => !r.differs01);
    console.log(
      anyFrozen
        ? '[t-dep] VERDICT: at least one non-forming treatment is FROZEN at this t — real JS-level t-threading bug.'
        : '[t-dep] VERDICT: every treatment is t-DEPENDENT in JS double precision (rotate() delta nonzero for ' +
          'all 7 types). This rules out a JS-level t-threading bug ONLY — it says nothing about whether the ' +
          'painted pixels actually move on screen (see this file\'s own header caveat: a double-precision check ' +
          'cannot see a Skia/GLSL float32 collapse). If a live canvas still looks static, chase it with ' +
          '`tableau-freeze-repro.spec.ts` (real Chromium), not this file.'
    );
    expect(rows.length).toBe(TYPES.length);
    expect(anyFrozen).toBe(false);
  });
});
