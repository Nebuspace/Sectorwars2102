/**
 * starShader — starVisualParams() is the module's only real logic (the
 * GLSL strings are static, framework-light by design per the file's own
 * header). Covers the DEFAULT_PARAMS/KIND_OVERRIDES layering, the
 * unrecognized-kind fallback to G_YELLOW's defaults, BLACK_HOLE's
 * isBlackHole flag, and the internal (unexported) hexToUnitRgb/
 * lighten/darken helpers via their only observable surface —
 * colorMid/colorCore/colorSpot on the returned params.
 */
import { describe, it, expect } from 'vitest';
import { starVisualParams, STAR_KINDS, type StarKind } from '../starShader';

describe('starVisualParams — G_YELLOW (the Sol-like default)', () => {
  it('carries every DEFAULT_PARAMS value unchanged, since G_YELLOW has no override', () => {
    const p = starVisualParams('G_YELLOW', '#ffcc33');
    expect(p.granulationScale).toBe(2.4);
    expect(p.granulationContrast).toBe(0.9);
    expect(p.domainWarpStrength).toBe(0.6);
    expect(p.limbPower).toBe(1.4);
    expect(p.coronaReach).toBe(1.35);
    expect(p.flareRate).toBe(0.35);
    expect(p.sunspotAmount).toBe(0.5);
    expect(p.cmeRate).toBe(0.4);
    expect(p.lensingStrength).toBe(0.0);
    expect(p.isBlackHole).toBe(false);
  });
});

describe('starVisualParams — kind overrides layer onto the defaults', () => {
  it('M_DWARF overrides granulation/corona/flare/cme but keeps default limbPower/lensingStrength', () => {
    const p = starVisualParams('M_DWARF', '#ff8844');
    expect(p.granulationScale).toBe(3.2);
    expect(p.granulationContrast).toBe(0.7);
    expect(p.coronaReach).toBe(1.18);
    expect(p.flareRate).toBe(0.55);
    expect(p.cmeRate).toBe(0.25);
    expect(p.limbPower).toBe(1.4); // unoverridden, inherited from DEFAULT_PARAMS
    expect(p.lensingStrength).toBe(0.0);
  });

  it('O_BLUE_SUPER runs hottest: widest corona, highest flare/cme, sharpest limb', () => {
    const p = starVisualParams('O_BLUE_SUPER', '#aaccff');
    expect(p.coronaReach).toBe(1.7);
    expect(p.flareRate).toBe(0.8);
    expect(p.cmeRate).toBe(0.8);
    expect(p.limbPower).toBe(1.1);
  });

  it('RED_GIANT gets coarse/slow granulation, heavy sunspots, dim flares', () => {
    const p = starVisualParams('RED_GIANT', '#ff5533');
    expect(p.granulationScale).toBe(1.1);
    expect(p.domainWarpStrength).toBe(0.9);
    expect(p.sunspotAmount).toBe(0.75);
    expect(p.flareRate).toBe(0.2);
    expect(p.limbPower).toBe(1.6);
  });

  it('WHITE_DWARF and NEUTRON both run fine-grained/high-contrast with a near-zero corona', () => {
    const dwarf = starVisualParams('WHITE_DWARF', '#eeeeff');
    expect(dwarf.granulationScale).toBe(4.0);
    expect(dwarf.sunspotAmount).toBe(0.1);
    expect(dwarf.coronaReach).toBe(1.1);
    expect(dwarf.cmeRate).toBe(0.05);

    const neutron = starVisualParams('NEUTRON', '#ccddff');
    expect(neutron.granulationScale).toBe(5.0);
    expect(neutron.sunspotAmount).toBe(0.0);
    expect(neutron.cmeRate).toBe(0.0);
    expect(neutron.limbPower).toBe(2.8);
  });
});

describe('starVisualParams — BLACK_HOLE', () => {
  it('is the only kind with isBlackHole true, and carries a non-zero lensingStrength', () => {
    const p = starVisualParams('BLACK_HOLE', '#ff9933');
    expect(p.isBlackHole).toBe(true);
    expect(p.lensingStrength).toBe(0.6);
    expect(p.coronaReach).toBe(1.45);
  });

  it.each(STAR_KINDS.filter((k): k is Exclude<StarKind, 'BLACK_HOLE'> => k !== 'BLACK_HOLE'))(
    '%s is not a black hole',
    (kind) => {
      expect(starVisualParams(kind, '#888888').isBlackHole).toBe(false);
    }
  );
});

describe('starVisualParams — unrecognized kind fallback', () => {
  it('falls back to G_YELLOW-equivalent defaults for an unknown kind string', () => {
    const p = starVisualParams('SOME_FUTURE_KIND_NOT_YET_SHIPPED', '#123456');
    const gYellow = starVisualParams('G_YELLOW', '#123456');
    expect(p.granulationScale).toBe(gYellow.granulationScale);
    expect(p.coronaReach).toBe(gYellow.coronaReach);
    expect(p.isBlackHole).toBe(false);
  });

  it('falls back the same way for an empty-string kind', () => {
    const p = starVisualParams('', '#123456');
    expect(p.granulationScale).toBe(2.4);
    expect(p.isBlackHole).toBe(false);
  });
});

describe('starVisualParams — color derivation (hexToUnitRgb/lighten/darken)', () => {
  it('parses a plain 6-digit hex to a 0..1 RGB triple', () => {
    const p = starVisualParams('G_YELLOW', '#ffffff');
    expect(p.colorMid).toEqual([1, 1, 1]);
  });

  it('accepts a hex without the leading #', () => {
    const p = starVisualParams('G_YELLOW', '000000');
    expect(p.colorMid).toEqual([0, 0, 0]);
  });

  it('is case-insensitive on the hex digits', () => {
    const upper = starVisualParams('G_YELLOW', '#FFAA00');
    const lower = starVisualParams('G_YELLOW', '#ffaa00');
    expect(upper.colorMid).toEqual(lower.colorMid);
  });

  it('falls back to a neutral mid-grey for an unparseable color rather than throwing', () => {
    expect(() => starVisualParams('G_YELLOW', 'not-a-color')).not.toThrow();
    const p = starVisualParams('G_YELLOW', 'not-a-color');
    expect(p.colorMid).toEqual([0.6, 0.6, 0.6]);
  });

  it('falls back to the same neutral grey for an empty color string', () => {
    const p = starVisualParams('G_YELLOW', '');
    expect(p.colorMid).toEqual([0.6, 0.6, 0.6]);
  });

  it('lightens toward white for colorCore and darkens toward black for colorSpot, from black', () => {
    const p = starVisualParams('G_YELLOW', '#000000');
    // lighten(0, 0.55) = 0 + (1-0)*0.55 = 0.55; darken(0, 0.65) = 0*(0.35) = 0
    expect(p.colorCore).toEqual([0.55, 0.55, 0.55]);
    expect(p.colorSpot).toEqual([0, 0, 0]);
  });

  it('lightens toward white for colorCore (already saturated, no headroom) and darkens colorSpot, from white', () => {
    const p = starVisualParams('G_YELLOW', '#ffffff');
    // lighten(1, 0.55) = 1 + (1-1)*0.55 = 1; darken(1, 0.65) = 1*(1-0.65) = 0.35
    expect(p.colorCore).toEqual([1, 1, 1]);
    expect(p.colorSpot).toEqual([0.35, 0.35, 0.35]);
  });

  it('derives colorCore/colorSpot from the SAME per-star color for every kind (not a fixed palette)', () => {
    const a = starVisualParams('M_DWARF', '#204080');
    const b = starVisualParams('O_BLUE_SUPER', '#204080');
    expect(a.colorMid).toEqual(b.colorMid);
    expect(a.colorCore).toEqual(b.colorCore);
    expect(a.colorSpot).toEqual(b.colorSpot);
  });
});

describe('starVisualParams — every catalogued kind produces a finite, usable param set', () => {
  it.each(STAR_KINDS)('%s returns finite numeric uniforms', (kind) => {
    const p = starVisualParams(kind, '#7799bb');
    for (const key of [
      'granulationScale',
      'granulationContrast',
      'domainWarpStrength',
      'limbPower',
      'coronaReach',
      'flareRate',
      'sunspotAmount',
      'cmeRate',
      'lensingStrength',
    ] as const) {
      expect(Number.isFinite(p[key])).toBe(true);
    }
    expect(p.colorCore).toHaveLength(3);
    expect(p.colorMid).toHaveLength(3);
    expect(p.colorSpot).toHaveLength(3);
  });
});
