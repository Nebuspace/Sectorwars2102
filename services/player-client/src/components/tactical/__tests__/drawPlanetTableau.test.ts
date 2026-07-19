/**
 * drawPlanetTableau — DB-free, DOM-light smoke proof (WO-AAA-SOLAR-TABLEAU
 * phase 2). Mirrors SolarSystemViewscreen.livingWindshield.test.tsx's own
 * no-op-ctx convention (Proxy stand-in with real-shaped measureText/
 * createRadialGradient/createLinearGradient) so this pure draw entry can be
 * exercised without a real canvas — visual fidelity is the hub's own
 * browser-prove (WO's Proof section), this only proves the code PATH is
 * throw-free for every treatment + the rings/forming/owned branches.
 */
import { describe, it, expect } from 'vitest';
import { drawPlanetTableau, treatmentFor, type Treatment, type TableauPlanetBody } from '../drawPlanetTableau';
import { pctToPx } from '../tableauFxHarness';
import type { SystemBody } from '../SolarSystemViewscreen';

// ---------------------------------------------------------------------------
// No-op CanvasRenderingContext2D — every draw call is a black hole; the few
// properties read as VALUES get real-shaped stand-ins (matches
// SolarSystemViewscreen.livingWindshield.test.tsx's makeNoopCtx exactly).
// ---------------------------------------------------------------------------

function makeNoopCtx(): CanvasRenderingContext2D {
  const store: Record<string, unknown> = {};
  return new Proxy(store, {
    get(target, prop) {
      if (prop === 'measureText') return () => ({ width: 10 });
      if (prop === 'createRadialGradient' || prop === 'createLinearGradient') {
        return () => ({ addColorStop: () => {} });
      }
      if (prop in target) return target[prop as string];
      return () => {};
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

const W = 1440;
const H = 334.7;
const mapper = (xPct: number, yPct: number) => pctToPx(xPct, yPct, W, H);

const KINDS: string[] = ['GAS_GIANT', 'BARREN', 'ICE', 'VOLCANIC', 'DESERT', 'TERRAN', 'OCEANIC'];

function makeBody(overrides: Partial<SystemBody> = {}): SystemBody {
  return {
    slot: 0,
    orbit_au: 0.4,
    kind: 'TERRAN',
    size_class: 2,
    palette: { hue: 140, sat: 55 },
    rings: false,
    moons: 1,
    phase_deg: 30,
    real: true,
    planet_id: 'planet-1',
    name: 'Test World',
    habitability: 62,
    owned: false,
    formation_status: undefined,
    ...overrides,
  };
}

function makePlanet(body: SystemBody, xPct = 40, yPct = 50, rPx = 14): TableauPlanetBody {
  return { body, xPct, yPct, rPx };
}

describe('treatmentFor', () => {
  it('maps every known kind to one of the 7 treatments', () => {
    const results = new Set<Treatment>();
    for (const k of KINDS) results.add(treatmentFor(k));
    expect(results.size).toBe(7);
  });

  it('falls back to BARREN for unknown/legacy kinds', () => {
    expect(treatmentFor('MOUNTAINOUS')).toBe('BARREN');
    expect(treatmentFor('UNKNOWN_XYZ')).toBe('BARREN');
  });
});

describe('drawPlanetTableau — throw-free for every surface + gate combination', () => {
  const star = { xPct: 12, yPct: 45 };

  it.each(KINDS)('draws the %s treatment without throwing', (kind) => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind });
    expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], 12.3, mapper, star)).not.toThrow();
  });

  it('draws a ringed body (back+front) without throwing', () => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind: 'GAS_GIANT', rings: true });
    expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], 5, mapper, star)).not.toThrow();
  });

  it('draws a forming (genesis terraforming) body without throwing', () => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind: 'TERRAN', formation_status: 'forming' });
    expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], 3.7, mapper, star)).not.toThrow();
  });

  it('draws an owned (city-lights) body without throwing, across several t values', () => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind: 'TERRAN', owned: true });
    for (const t of [0, 1.5, 9, 42]) {
      expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], t, mapper, star)).not.toThrow();
    }
  });

  it('draws an owned + ringed + forming body (every gate at once) without throwing', () => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind: 'OCEANIC', owned: true, rings: true, formation_status: 'forming' });
    expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], 8.1, mapper, star)).not.toThrow();
  });

  it('freezes cleanly at t=0 (reduced-motion single frame) for every treatment', () => {
    const ctx = makeNoopCtx();
    for (const kind of KINDS) {
      const body = makeBody({ kind, owned: true, rings: kind === 'ICE' });
      expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], 0, mapper, star)).not.toThrow();
    }
  });

  it('handles a null star (star-less snapshot) without throwing', () => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind: 'VOLCANIC' });
    expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body)], 4, mapper, null)).not.toThrow();
  });

  it('skips a zero-radius body (band not yet measured) without throwing', () => {
    const ctx = makeNoopCtx();
    const body = makeBody({ kind: 'DESERT' });
    expect(() => drawPlanetTableau(ctx, 7, [makePlanet(body, 40, 50, 0)], 1, mapper, star)).not.toThrow();
  });

  it('draws a full mixed system (all 7 kinds together) without throwing', () => {
    const ctx = makeNoopCtx();
    const planets = KINDS.map((kind, i) =>
      makePlanet(makeBody({ slot: i, kind, rings: i % 3 === 0, owned: i % 2 === 0 }), 20 + i * 8, 40 + i * 5, 10 + i)
    );
    expect(() => drawPlanetTableau(ctx, 99, planets, 17.2, mapper, star)).not.toThrow();
  });
});
