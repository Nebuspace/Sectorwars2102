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
  safeOrbitRadii,
  DECORATIVE_RING_RADII,
  MOON_DOT_MIN_EM,
  MOON_DOT_MAX_EM,
  STAR_MIN_SIZE_VS_LARGEST_PLANET,
  BODY_SIZE_EM_MAX,
  ORBIT_AU_MAX,
  type BandGeometry,
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

  // WO-TABLEAU-TUNE (live-playtest #18): "represent the star... as MUCH
  // LARGER than the planets" — must hold for EVERY star kind, not just the
  // naturally-huge ones, against whatever bodies are actually in the system.
  it('is floored at STAR_MIN_SIZE_VS_LARGEST_PLANET x the largest real body present, even for a modest star kind', () => {
    const bodies = [
      { ...BODY, slot: 0, size_class: 3 }, // bodySizeEm ≈ 1.39
      { ...BODY, slot: 1, size_class: 10 }, // bodySizeEm = 2.4 (ceiling) — the largest
    ];
    const largest = Math.max(...bodies.map(bodySizeEm));
    const star = starAnchor(9, { kind: 'G_YELLOW', label: '', color: '#fff' }, bodies);
    expect(star.sizeEm).toBeGreaterThanOrEqual(largest * STAR_MIN_SIZE_VS_LARGEST_PLANET);
  });

  it('still floors correctly with zero bodies (no system data yet)', () => {
    const star = starAnchor(9, { kind: 'G_YELLOW', label: '', color: '#fff' }, []);
    expect(star.sizeEm).toBeGreaterThan(0);
  });

  it('a naturally giant star kind is unaffected by the floor (already clears it on its own factor)', () => {
    const bodies = [{ ...BODY, slot: 0, size_class: 10 }]; // bodySizeEm = 2.4 (ceiling)
    const floor = bodySizeEm(bodies[0]) * STAR_MIN_SIZE_VS_LARGEST_PLANET;
    const dwarfStar = starAnchor(9, { kind: 'M_DWARF', label: '', color: '#fff' }, bodies);
    const giantStar = starAnchor(9, { kind: 'RED_GIANT', label: '', color: '#fff' }, bodies);
    expect(dwarfStar.sizeEm).toBeCloseTo(floor, 1); // clamped up to the floor (rounded to 1dp)
    expect(giantStar.sizeEm).toBeGreaterThan(floor); // already above it on its own factor
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

  // ---- WO-TABLEAU-TUNE (live-playtest #17): moon families ----------------

  it('every moon of ONE planet co-rotates — a single shared direction, not independently random per moon', () => {
    const family: SystemBody = { ...BODY, moons: 5 };
    const orbits = moonOrbits(7, family);
    const directions = new Set(orbits.map((o) => o.clockwise));
    expect(directions.size).toBe(1);
  });

  it('family direction is deterministic per planet id (body.slot) and MAY differ planet-to-planet', () => {
    const bodyA: SystemBody = { ...BODY, slot: 0, moons: 2 };
    // Deterministic: same body -> same direction on a second, independent call.
    expect(moonOrbits(7, bodyA)[0].clockwise).toBe(moonOrbits(7, { ...bodyA })[0].clockwise);
    // Across a spread of slots, both directions actually occur (not hardcoded
    // to always-clockwise or always-counter) — proves per-planet variation
    // is real, not coincidental to a single sample.
    const seen = new Set(
      Array.from({ length: 12 }, (_, slot) => moonOrbits(7, { ...BODY, slot, moons: 1 })[0].clockwise)
    );
    expect(seen.size).toBe(2);
  });

  it('consecutive orbital tracks never compete — the radial gap always clears MOON_DOT_MAX_EM (the largest possible moon-dot diameter)', () => {
    const family: SystemBody = { ...BODY, moons: 6 };
    const radii = moonOrbits(7, family).map((o) => o.radiusEm).sort((a, b) => a - b);
    for (let i = 1; i < radii.length; i++) {
      expect(radii[i] - radii[i - 1]).toBeGreaterThan(MOON_DOT_MAX_EM);
    }
  });

  it('moon-dot sizes are varied within a family and stay within the MOON_DOT_MIN_EM..MOON_DOT_MAX_EM band', () => {
    const family: SystemBody = { ...BODY, moons: 6 };
    const sizes = moonOrbits(7, family).map((o) => o.sizeEm);
    for (const s of sizes) {
      expect(s).toBeGreaterThanOrEqual(MOON_DOT_MIN_EM);
      expect(s).toBeLessThanOrEqual(MOON_DOT_MAX_EM);
    }
    expect(new Set(sizes.map((s) => Math.round(s * 1000))).size).toBeGreaterThan(1); // not all identical
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

// ---- T1-A (Max live-playtest): every body/station must stay in-band -------

describe('safeOrbitRadii / orbitalPosition(safeRadii) — T1-A in-band invariant', () => {
  // A representative WIDE-SHORT band, computed from the real flight-mode
  // formula this component actually renders into at 1440x900 (cockpit-
  // shell.css `.band{--band-h-flight:18.5em}` + game-layout.css's
  // `div.game-container{font-size:calc(clamp(10px,0.3vw+1.53vh,24px)*var(--uiscale))}`
  // resolved at 1440x900, uiscale=1: 0.3*14.4 + 1.53*9 = 18.09px root em,
  // band height = 18.5 * 18.09 ~= 334.7px; band width = the full stage
  // width, ~1440px (the band row has no column split — game-layout.css's
  // `.lower{grid-template-columns:19% 81%}` split only applies one row
  // down). A second, TIGHTER geometry (ARIA-2 panel mode, 12.5em) is swept
  // too below, so this isn't tuned to one specific height.
  const FLIGHT_BAND: BandGeometry = { widthPx: 1440, heightPx: 334.7, remPx: 18.09 };
  const ARIA2_BAND: BandGeometry = { widthPx: 1440, heightPx: 226.1, remPx: 18.09 }; // 12.5em

  // Any actual body/station footprint this component ever renders — the
  // same ceiling WindshieldTableau.tsx passes (its own OBJECT_FOOTPRINT_EM_MAX).
  const MAX_OBJECT_EM = 3.2;

  it('the footprint ceiling used below stays a superset of BODY_SIZE_EM_MAX (drift guard)', () => {
    expect(MAX_OBJECT_EM).toBeGreaterThanOrEqual(BODY_SIZE_EM_MAX);
  });

  const STEP_AU = 0.02;
  const STEP_DEG = 2;

  function assertInBand(band: BandGeometry, sectorSamples: number[], emWidth = MAX_OBJECT_EM, emHeight = emWidth) {
    const halfObjXPct = ((emWidth / 2) * band.remPx / band.widthPx) * 100;
    const halfObjYPct = ((emHeight / 2) * band.remPx / band.heightPx) * 100;
    for (const sectorId of sectorSamples) {
      const star = starAnchor(sectorId, null);
      const radii = safeOrbitRadii(star, band, emWidth, emHeight);
      for (let au = 0.2; au <= ORBIT_AU_MAX + 1e-9; au += STEP_AU) {
        for (let deg = 0; deg < 360; deg += STEP_DEG) {
          const pos = orbitalPosition(star, au, deg, radii);
          // The FULL rendered rect (center +/- half footprint) must stay
          // inside [0,100]% on both axes -- not just the center point.
          expect(pos.xPct - halfObjXPct).toBeGreaterThanOrEqual(-1e-6);
          expect(pos.xPct + halfObjXPct).toBeLessThanOrEqual(100 + 1e-6);
          expect(pos.yPct - halfObjYPct).toBeGreaterThanOrEqual(-1e-6);
          expect(pos.yPct + halfObjYPct).toBeLessThanOrEqual(100 + 1e-6);
        }
      }
    }
  }

  it('every (orbit_au, phase_deg) in the live contract range stays fully in-band, across a spread of sectors, at the flight-mode band height', () => {
    assertInBand(FLIGHT_BAND, [1, 2, 5, 9, 21, 40, 77]); // 21 = the live symptom sector; 77 = the WindshieldTableau.test.tsx fixture sector
  }, 20_000);

  it('also holds at the tighter ARIA-2 panel-mode band height (12.5em) -- the fix isn\'t tuned to one specific height', () => {
    assertInBand(ARIA2_BAND, [1, 21, 77]);
  }, 20_000);

  // ---- station-scale footprint (WindshieldTableau.tsx's own
  // STATION_FOOTPRINT_EM_WIDTH_MAX/HEIGHT_MAX) — a MUCH wider margin than a
  // planet disc needs, which surfaced a real edge case a live Playwright
  // proof caught: at cos(phase_deg)=0 (or sin=0) the per-quadrant radius
  // contributes NOTHING to that axis, so a star anchored close to an edge
  // (starAnchor's own 9-14% left range) can itself sit inside a wide
  // object's margin — no radius scaling fixes that, only orbitalPosition's
  // final xMinPct/xMaxPct/yMinPct/yMaxPct hard clamp does (SafeOrbitRadii's
  // own doc-comment). Sweeps sectors 0-40 (not just the same handful above)
  // specifically to hit a spread of starAnchor's own xPct/yPct rolls,
  // including ones close to its floor. */
  it('holds at station-scale footprint margins too (20em wide x 5em tall) -- the star-anchor-inside-the-margin edge case a live proof caught', () => {
    const sectors = Array.from({ length: 41 }, (_, i) => i); // 0..40
    assertInBand(FLIGHT_BAND, sectors, 20, 5);
  }, 20_000);

  it('an out-of-contract orbit_au beyond ORBIT_AU_MAX is defensively clamped, not extrapolated past the safe box', () => {
    const star = starAnchor(21, null);
    const radii = safeOrbitRadii(star, FLIGHT_BAND, MAX_OBJECT_EM);
    const atCeiling = orbitalPosition(star, ORBIT_AU_MAX, 0, radii);
    const beyond = orbitalPosition(star, ORBIT_AU_MAX + 5, 0, radii); // absurd stray value
    expect(beyond).toEqual(atCeiling);
  });

  it('without safeRadii, orbitalPosition is byte-identical to the pre-T1-A unclamped math (decorative callers, and any caller before a real band is measured, are unaffected)', () => {
    const star = starAnchor(3, null);
    const withoutRadii = orbitalPosition(star, 0.5, 40);
    const rx = 0.5 * 80; // AU_SEMI_X_PCT
    const ry = 0.5 * 120; // AU_SEMI_Y_PCT
    const rad = (40 * Math.PI) / 180;
    expect(withoutRadii.xPct).toBeCloseTo(star.xPct + Math.cos(rad) * rx);
    expect(withoutRadii.yPct).toBeCloseTo(star.yPct + Math.sin(rad) * ry);
  });

  it('bodyPosition/stationPosition forward safeRadii through to orbitalPosition unchanged', () => {
    const star = starAnchor(21, null);
    const radii = safeOrbitRadii(star, FLIGHT_BAND, MAX_OBJECT_EM);
    const body = { slot: 0, orbit_au: 0.6, kind: 'TERRAN', size_class: 4, palette: { hue: 0, sat: 0 }, rings: false, moons: 0, phase_deg: 200, real: true, planet_id: 'p', name: 'X' };
    const station = { station_id: 's', name: 'S', type: 'trading_post', orbit_au: 0.6, phase_deg: 200 };
    expect(bodyPosition(star, body, radii)).toEqual(orbitalPosition(star, body.orbit_au, body.phase_deg, radii));
    expect(stationPosition(star, station, radii)).toEqual(orbitalPosition(star, station.orbit_au, station.phase_deg, radii));
  });

  it('degrades to zero radius (never negative) for a direction with no usable room', () => {
    // A star pinned at the very edge with a huge footprint eats all the room.
    const tinyBand: BandGeometry = { widthPx: 50, heightPx: 50, remPx: 18 };
    const radii = safeOrbitRadii({ xPct: 1, yPct: 1, sizeEm: 4 }, tinyBand, MAX_OBJECT_EM);
    expect(radii.leftPctPerAu).toBe(0);
    expect(radii.upPctPerAu).toBe(0);
    expect(radii.rightPctPerAu).toBeGreaterThanOrEqual(0);
    expect(radii.downPctPerAu).toBeGreaterThanOrEqual(0);
  });
});

// ---- T0-1 (Max live-catch, sector 1): bodies must stay DISTINCT, not just
// in-band -- the hole T1-A's own review missed. All-left-hemisphere-phase
// data collapsed onto the far-left-anchored star's own xPct regardless of
// orbit_au (leftPctPerAu~=0 by construction there); fixed by making X
// primarily orbit_au-driven (see orbitalPosition's own T0-1 doc-comment).

describe('T0-1 — bodies stay DISTINCT and SPREAD, not just in-band (sector-1 live-catch)', () => {
  const FLIGHT_BAND: BandGeometry = { widthPx: 1440, heightPx: 334.7, remPx: 18.09 };
  const PLANET_EM = 2.6; // mirrors WindshieldTableau.tsx's own PLANET_FOOTPRINT_EM_MAX

  // Max's own live repro, verbatim: sector 1, all 6 bodies at cos(phase)<0
  // (118deg/119deg/251deg/228deg/160deg/135deg are all in the left
  // hemisphere) -- the exact input that piled onto the star pre-fix.
  const SECTOR_1_BODIES: SystemBody[] = [
    { slot: 0, orbit_au: 0.2507, kind: 'BARREN', size_class: 3, palette: { hue: 30, sat: 20 }, rings: false, moons: 0, phase_deg: 118, real: false },
    { slot: 1, orbit_au: 0.4176, kind: 'TERRAN', size_class: 5, palette: { hue: 120, sat: 45 }, rings: false, moons: 1, phase_deg: 119, real: true, planet_id: 'new-earth', name: 'New Earth' },
    { slot: 2, orbit_au: 0.5784, kind: 'GAS_GIANT', size_class: 8, palette: { hue: 40, sat: 55 }, rings: true, moons: 2, phase_deg: 251, real: false },
    { slot: 3, orbit_au: 0.6802, kind: 'BARREN', size_class: 4, palette: { hue: 25, sat: 15 }, rings: false, moons: 0, phase_deg: 228, real: false },
    { slot: 4, orbit_au: 0.8275, kind: 'VOLCANIC', size_class: 6, palette: { hue: 10, sat: 60 }, rings: false, moons: 0, phase_deg: 160, real: false },
    { slot: 5, orbit_au: 0.9438, kind: 'GAS_GIANT', size_class: 9, palette: { hue: 200, sat: 50 }, rings: true, moons: 3, phase_deg: 135, real: false },
  ];

  // A "sector-21-like" 7-body set with a full phase spread across all four
  // quadrants (not all-left) -- the no-regression case: T1-A's own good
  // spread on mixed-phase data must survive this redesign untouched.
  const SECTOR_21_LIKE_BODIES: SystemBody[] = [
    { slot: 0, orbit_au: 0.22, kind: 'BARREN', size_class: 3, palette: { hue: 30, sat: 20 }, rings: false, moons: 0, phase_deg: 20, real: false },
    { slot: 1, orbit_au: 0.35, kind: 'TERRAN', size_class: 4, palette: { hue: 120, sat: 45 }, rings: false, moons: 1, phase_deg: 95, real: true, planet_id: 'p1', name: 'World 1' },
    { slot: 2, orbit_au: 0.48, kind: 'ICE', size_class: 5, palette: { hue: 200, sat: 40 }, rings: false, moons: 0, phase_deg: 160, real: false },
    { slot: 3, orbit_au: 0.6, kind: 'GAS_GIANT', size_class: 7, palette: { hue: 40, sat: 55 }, rings: true, moons: 2, phase_deg: 210, real: false },
    { slot: 4, orbit_au: 0.72, kind: 'BARREN', size_class: 4, palette: { hue: 25, sat: 15 }, rings: false, moons: 0, phase_deg: 280, real: false },
    { slot: 5, orbit_au: 0.85, kind: 'VOLCANIC', size_class: 6, palette: { hue: 10, sat: 60 }, rings: false, moons: 0, phase_deg: 300, real: true, planet_id: 'p6', name: 'World 6' },
    { slot: 6, orbit_au: 0.93, kind: 'GAS_GIANT', size_class: 9, palette: { hue: 200, sat: 50 }, rings: true, moons: 3, phase_deg: 75, real: false },
  ];

  function assertDistinctAndSpread(sectorId: number, bodies: SystemBody[], label: string) {
    const star = starAnchor(sectorId, { kind: 'K_ORANGE', label: '', color: '#fff' }, bodies);
    const radii = safeOrbitRadii(star, FLIGHT_BAND, PLANET_EM);
    const placed = bodies.map((b) => {
      const pos = bodyPosition(star, b, radii);
      return {
        xPx: (pos.xPct / 100) * FLIGHT_BAND.widthPx,
        yPx: (pos.yPct / 100) * FLIGHT_BAND.heightPx,
        diamPx: bodySizeEm(b) * FLIGHT_BAND.remPx,
      };
    });

    // 1. IN-BAND (T1-A, must survive this redesign).
    for (const p of placed) {
      expect(p.xPx - p.diamPx / 2).toBeGreaterThanOrEqual(-0.5);
      expect(p.xPx + p.diamPx / 2).toBeLessThanOrEqual(FLIGHT_BAND.widthPx + 0.5);
      expect(p.yPx - p.diamPx / 2).toBeGreaterThanOrEqual(-0.5);
      expect(p.yPx + p.diamPx / 2).toBeLessThanOrEqual(FLIGHT_BAND.heightPx + 0.5);
    }

    // 2. DISTINCT -- min pairwise center-to-center distance >= 1.2x the
    // LARGER of the two bodies' own diameters.
    let minDist = Infinity;
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const dx = placed[i].xPx - placed[j].xPx;
        const dy = placed[i].yPx - placed[j].yPx;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const threshold = 1.2 * Math.max(placed[i].diamPx, placed[j].diamPx);
        minDist = Math.min(minDist, dist);
        expect(dist, `${label}: bodies ${i}/${j} too close (${dist.toFixed(1)}px < ${threshold.toFixed(1)}px)`).toBeGreaterThanOrEqual(threshold);
      }
    }

    // 3. SPREAD -- x-centers span >= 50% of the band width.
    const xs = placed.map((p) => p.xPx);
    const xRange = Math.max(...xs) - Math.min(...xs);
    expect(xRange, `${label}: x-range ${xRange.toFixed(1)}px`).toBeGreaterThanOrEqual(FLIGHT_BAND.widthPx * 0.5);

    return { minDist, xRangePx: xRange, placed };
  }

  it('sector-1 repro (all 6 bodies left-hemisphere phase): stays in-band, all 6 DISTINCT, x-range >=50% of band width', () => {
    const { minDist, xRangePx } = assertDistinctAndSpread(1, SECTOR_1_BODIES, 'sector-1');
    // eslint-disable-next-line no-console
    console.log(`[T0-1 proof] sector-1: minPairwiseDist=${minDist.toFixed(1)}px, xRange=${xRangePx.toFixed(1)}px (band width ${FLIGHT_BAND.widthPx}px)`);
    expect(minDist).toBeGreaterThan(0);
  });

  it('sector-21-like 7-body mixed-phase case: no regression -- stays in-band, distinct, and well-spread', () => {
    const { minDist, xRangePx } = assertDistinctAndSpread(21, SECTOR_21_LIKE_BODIES, 'sector-21-like');
    // eslint-disable-next-line no-console
    console.log(`[T0-1 proof] sector-21-like: minPairwiseDist=${minDist.toFixed(1)}px, xRange=${xRangePx.toFixed(1)}px (band width ${FLIGHT_BAND.widthPx}px)`);
    expect(minDist).toBeGreaterThan(0);
  });

  it('X is monotonic in orbit_au at a fixed phase (the "further out = further right" fan, independent of the old left/right radius branch)', () => {
    const star = starAnchor(1, { kind: 'K_ORANGE', label: '', color: '#fff' }, SECTOR_1_BODIES);
    const radii = safeOrbitRadii(star, FLIGHT_BAND, PLANET_EM);
    const xs = [0.25, 0.4, 0.55, 0.7, 0.85, 0.94].map((au) => orbitalPosition(star, au, 200, radii).xPct); // same phase for all -- isolates the orbit_au term
    for (let i = 1; i < xs.length; i++) {
      expect(xs[i]).toBeGreaterThan(xs[i - 1]);
    }
  });
});
