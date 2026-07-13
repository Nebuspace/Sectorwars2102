/**
 * windshieldTableauLayout — pure-function proof (WO-UI2-WINDSHIELD-TABLEAU).
 * Zero DOM, zero React — determinism + geometry only.
 */
import { describe, it, expect } from 'vitest';
import {
  starAnchor,
  decorativeRings,
  beltStyle,
  orbitalPosition,
  bodyPosition,
  stationPosition,
  moonOrbits,
  bodySizeEm,
  scanPosition,
  otherPresencePosition,
  selfRestingAnchor,
  headingDeg,
  nebulaArcs,
  debrisArc,
  DECORATIVE_RING_RADII,
} from '../windshieldTableauLayout';
import type { SystemBody, SystemStation } from '../SolarSystemViewscreen';

const BODY: SystemBody = {
  slot: 0, orbit_au: 0.5, kind: 'TERRAN', size_class: 3,
  palette: { hue: 100, sat: 40 }, rings: false, moons: 3, phase_deg: 90,
  real: true, planet_id: 'p1', name: 'Test World',
};

const STATION: SystemStation = { station_id: 's1', name: 'Dock', type: 'trading_post', orbit_au: 0.6, phase_deg: 180 };

describe('starAnchor', () => {
  it('is deterministic per sectorId', () => {
    const a = starAnchor(5, null);
    const b = starAnchor(5, null);
    expect(a).toEqual(b);
  });

  it('anchors off-center-left (a "sliver", not a centered orrery)', () => {
    const a = starAnchor(5, null);
    expect(a.xPct).toBeGreaterThanOrEqual(9);
    expect(a.xPct).toBeLessThanOrEqual(14);
    expect(a.yPct).toBeGreaterThanOrEqual(42);
    expect(a.yPct).toBeLessThanOrEqual(50);
  });

  it('differs across sectors (not one shared skeleton)', () => {
    const a = starAnchor(1, null);
    const b = starAnchor(2, null);
    expect(a).not.toEqual(b);
  });

  it('sizes by star kind (bigger for a blue supergiant than a red dwarf)', () => {
    const dwarf = starAnchor(9, { kind: 'M_DWARF', label: '', color: '#fff' });
    const giant = starAnchor(9, { kind: 'O_BLUE_SUPER', label: '', color: '#fff' });
    expect(giant.sizeEm).toBeGreaterThan(dwarf.sizeEm);
  });
});

describe('decorativeRings', () => {
  it('emits exactly the 4 demo-verbatim fixed AU marks, centered on the star', () => {
    const star = starAnchor(3, null);
    const rings = decorativeRings(star);
    expect(rings.length).toBe(DECORATIVE_RING_RADII.length);
    rings.forEach((r, i) => {
      expect(r.xPct).toBe(star.xPct);
      expect(r.yPct).toBe(star.yPct);
      expect(r.wPct).toBeCloseTo(DECORATIVE_RING_RADII[i] * 1.6);
      expect(r.hPct).toBeCloseTo(DECORATIVE_RING_RADII[i] * 2.4);
    });
  });
});

describe('beltStyle', () => {
  it('is centered on the star, mostly off-frame by design (>100%)', () => {
    const star = starAnchor(3, null);
    const belt = beltStyle(star);
    expect(belt.xPct).toBe(star.xPct);
    expect(belt.wPct).toBeGreaterThan(100);
    expect(belt.hPct).toBeGreaterThan(100);
  });
});

describe('orbitalPosition / bodyPosition / stationPosition', () => {
  it('has zero time term — same inputs always produce the same position (no system-level animation at rest)', () => {
    const star = starAnchor(3, null);
    const a = orbitalPosition(star, 0.5, 90);
    const b = orbitalPosition(star, 0.5, 90);
    expect(a).toEqual(b);
  });

  it('places the body on the star-centered elliptical plane via cos/sin of phase_deg', () => {
    const star = { xPct: 10, yPct: 45, sizeEm: 4 };
    const pos0 = orbitalPosition(star, 0.5, 0); // due "right" of the star
    expect(pos0.xPct).toBeGreaterThan(star.xPct);
    expect(pos0.yPct).toBeCloseTo(star.yPct);
    const pos90 = orbitalPosition(star, 0.5, 90); // due "below"
    expect(pos90.yPct).toBeGreaterThan(star.yPct);
    expect(pos90.xPct).toBeCloseTo(star.xPct);
  });

  it('bodyPosition/stationPosition are thin wrappers over orbitalPosition using orbit_au+phase_deg', () => {
    const star = starAnchor(3, null);
    expect(bodyPosition(star, BODY)).toEqual(orbitalPosition(star, BODY.orbit_au, BODY.phase_deg));
    expect(stationPosition(star, STATION)).toEqual(orbitalPosition(star, STATION.orbit_au, STATION.phase_deg));
  });
});

describe('moonOrbits', () => {
  it('returns exactly `moons` entries, deterministic per (sectorId, body.slot)', () => {
    const a = moonOrbits(7, BODY);
    const b = moonOrbits(7, BODY);
    expect(a.length).toBe(BODY.moons);
    expect(a).toEqual(b);
  });

  it('returns none for a body with moons=0', () => {
    expect(moonOrbits(7, { ...BODY, moons: 0 })).toEqual([]);
  });

  it('every orbit has a positive radius and duration (slow, subtle local motion)', () => {
    for (const m of moonOrbits(7, BODY)) {
      expect(m.radiusEm).toBeGreaterThan(0);
      expect(m.durationS).toBeGreaterThan(0);
    }
  });

  it('revolution duration is slow (40-90s/lap), not the erratic fast spin Max flagged (live-playtest #9)', () => {
    for (const m of moonOrbits(7, BODY)) {
      expect(m.durationS).toBeGreaterThanOrEqual(40);
      expect(m.durationS).toBeLessThanOrEqual(90);
    }
  });

  it('radius is scaled off the PARENT body\'s own rendered size — just outside its disc edge, not a flat detached value', () => {
    const smallBody: SystemBody = { ...BODY, size_class: 1, moons: 1 };
    const bigBody: SystemBody = { ...BODY, size_class: 10, moons: 1 };
    const smallRadiusEm = bodySizeEm(smallBody) / 2;
    const bigRadiusEm = bodySizeEm(bigBody) / 2;

    const [smallMoon] = moonOrbits(7, smallBody);
    const [bigMoon] = moonOrbits(7, bigBody);

    // Every moon sits OUTSIDE its own parent's disc (radius > the parent's
    // own radius), and — because the ratio to the parent scales with the
    // parent, not a fixed em — a small planet's moon never lands as far out
    // (in absolute em) as a big planet's, unlike a flat/detached radius.
    expect(smallMoon.radiusEm).toBeGreaterThan(smallRadiusEm);
    expect(bigMoon.radiusEm).toBeGreaterThan(bigRadiusEm);
    expect(smallMoon.radiusEm).toBeLessThan(bigMoon.radiusEm);
  });

  it('multiple moons on the same body stagger outward (no two share a radius)', () => {
    const multiMoon: SystemBody = { ...BODY, moons: 3 };
    const orbits = moonOrbits(7, multiMoon);
    const radii = orbits.map((o) => o.radiusEm);
    expect(new Set(radii.map((r) => Math.round(r * 1000))).size).toBe(radii.length);
  });
});

describe('bodySizeEm', () => {
  it('clamps to [0.9, 2.4]em and grows with size_class', () => {
    expect(bodySizeEm({ ...BODY, size_class: 0 })).toBeCloseTo(0.9); // floor
    expect(bodySizeEm({ ...BODY, size_class: 20 })).toBeCloseTo(2.4); // ceiling
    expect(bodySizeEm({ ...BODY, size_class: 5 })).toBeGreaterThan(bodySizeEm({ ...BODY, size_class: 2 }));
  });
});

describe('scanPosition / otherPresencePosition', () => {
  it('are deterministic per id and stay within the visible-ish scatter bounds', () => {
    const a = scanPosition('wreck-1');
    const b = scanPosition('wreck-1');
    expect(a).toEqual(b);
    expect(a.xPct).toBeGreaterThanOrEqual(8);
    expect(a.xPct).toBeLessThanOrEqual(92);

    const c = otherPresencePosition('ship-1');
    const d = otherPresencePosition('ship-1');
    expect(c).toEqual(d);
  });

  it('different ids land at different positions (no collision by construction... in practice)', () => {
    expect(scanPosition('wreck-1')).not.toEqual(scanPosition('wreck-2'));
  });
});

describe('selfRestingAnchor', () => {
  it('is deterministic per sectorId', () => {
    expect(selfRestingAnchor(11)).toEqual(selfRestingAnchor(11));
  });
});

describe('headingDeg', () => {
  it('is 0 for a stationary point', () => {
    const p = { xPct: 10, yPct: 10 };
    expect(headingDeg(p, p)).toBe(0);
  });

  it('points right (0deg) when moving due +x', () => {
    expect(headingDeg({ xPct: 0, yPct: 0 }, { xPct: 10, yPct: 0 })).toBeCloseTo(0);
  });

  it('points down (90deg) when moving due +y', () => {
    expect(headingDeg({ xPct: 0, yPct: 0 }, { xPct: 0, yPct: 10 })).toBeCloseTo(90);
  });
});

describe('nebulaArcs / debrisArc', () => {
  it('nebulaArcs is deterministic and returns 2-3 bands', () => {
    const a = nebulaArcs(4);
    const b = nebulaArcs(4);
    expect(a).toEqual(b);
    expect(a.length).toBeGreaterThanOrEqual(2);
    expect(a.length).toBeLessThanOrEqual(3);
  });

  it('debrisArc centers on the ring midpoint radius', () => {
    const arc = debrisArc({ inner_au: 0.4, outer_au: 0.8 });
    expect(arc.rFrac).toBeCloseTo(0.6);
  });
});
