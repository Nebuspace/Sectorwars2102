/**
 * windshieldTableauLayout — pure, deterministic %-space layout math for the
 * WindshieldTableau (WO-UI2-WINDSHIELD-TABLEAU). No React, no DOM, no fetch —
 * every export here is a pure function of (sectorId, snapshot data) so it can
 * be unit-tested in isolation and so "same sector → same layout" holds by
 * construction (no wall-clock, no Math.random()).
 *
 * Composition mirrors the ratified demo's renderBand (space mode) —
 * audit/design-briefs/cockpit-redesign-v10-RATIFIED.html L1214-1263 — but
 * feeds it from the REAL SystemSnapshot contract (SolarSystemViewscreen.tsx's
 * SystemBody/SystemStation: orbit_au + phase_deg) instead of the demo's
 * hand-authored per-sector x/y. The star anchors OFF-CENTER (a "sliver" of
 * the system, not a centered orrery — Max, live-playtest #4) and bodies are
 * placed on that off-center orbital plane via elliptical projection; nothing
 * here reads the wall clock, so the SYSTEM-level composition never animates
 * at rest. Only the ship marker (owned by WindshieldTableau.tsx) and moon
 * child-orbits (CSS-driven, see solar-system-viewscreen.css) move.
 *
 * PRNG: reuses the vista engine's SplitMix32 (vista/core/rng.ts) — the same
 * algorithm SolarSystemViewscreen.tsx's own local `splitmix32` implements
 * (see that file's header comment on vista/core/rng.ts), so this module has
 * zero duplicate PRNG code and stays dependency-light (no import of the
 * 8000+ line canvas file — type-only imports only, erased at build time).
 *
 * T1-A addendum: orbitalPosition/bodyPosition/stationPosition now take an
 * OPTIONAL real-band-geometry input (BandGeometry, via safeOrbitRadii's
 * SafeOrbitRadii) so a real body/station can never land outside the band's
 * visible [0,100]%x[0,100]% — still a pure function of its (now slightly
 * larger) input set, still zero DOM access itself; see safeOrbitRadii's own
 * doc-comment for the full rationale.
 */
import { SeededRng, deriveChildSeed } from '../../vista/core/rng';
import type { SystemBody, SystemStation, SystemStar } from './SolarSystemViewscreen';
import { STAR_RADIUS_FACTOR } from './SolarSystemViewscreen';

// A single namespace for every seed this module derives, so no two callers
// can accidentally collide on the same child-stream name.
const NS = 'windshield-tableau';

export interface PctPoint {
  xPct: number;
  yPct: number;
}

export interface StarAnchor extends PctPoint {
  sizeEm: number;
}

/** Demo-verbatim decorative ring "radii" (NOT tied to real orbit_au — flat
 *  z-0 decoration only, RATIFIED.html L1219: w=r*1.6%, h=r*2.4%). */
export const DECORATIVE_RING_RADII: readonly number[] = [22, 42, 62, 82];

/** Semi-axis %-per-orbit_au scale for REAL body/station placement. orbit_au
 *  is already a normalized ~0.2-0.95 fraction (celestial_service.py), so
 *  treating it as "demo r / 100" and applying the demo's own w=r*1.6/h=r*2.4
 *  ring density keeps body placement visually consistent with the decorative
 *  rings above (a body at orbit_au≈0.22 sits near the 22-ring, ≈0.82 near
 *  the 82-ring) without inventing a second, disconnected scale. */
export const AU_SEMI_X_PCT = 80; // = 100 * 1.6 / 2
export const AU_SEMI_Y_PCT = 120; // = 100 * 2.4 / 2

/** Fallback "typical planet" size (em) used only to floor the star's size
 *  when a system has zero real/procedural bodies to compare against — the
 *  midpoint of bodySizeEm's own [0.9, 2.4] range. */
const STAR_SIZE_FALLBACK_PLANET_EM = 1.6;

/** Max, live-playtest #18: "represent the star... as MUCH LARGER than the
 *  planets" — the rendered star must clear this multiple of the LARGEST
 *  planet actually present in the system, regardless of star kind. 3.2 is a
 *  safety margin over the 3x floor so rounding never lands exactly at the
 *  boundary. */
export const STAR_MIN_SIZE_VS_LARGEST_PLANET = 3.2;

/** Off-center-left star anchor — the "sliver" (Max: "a sliver of the solar
 *  system... no rotating around the sun"). Ranges mirror the demo's own
 *  per-sector star.x/y authoring (RATIFIED.html L727-748: x 8-12, y 40-50 —
 *  VERIFIED against all 4 SEC entries live-playtest #18; a WO-TABLEAU-TUNE
 *  citation claiming "x 15-25" does not match any of the 4 authored sectors,
 *  so the existing 9-14% anchor — already the demo-verbatim range — is kept
 *  unchanged here), with a small sectorId-seeded jitter so systems don't
 *  share one skeleton (same intent as the live canvas's anchorRng, ported
 *  off-center).
 *
 *  `bodies` (optional, live-playtest #18 addendum): the system's real+
 *  procedural bodies, used ONLY to floor the star's rendered size at
 *  STAR_MIN_SIZE_VS_LARGEST_PLANET x the largest one actually present — the
 *  live render read "too modest/planet-like" because the per-star-kind
 *  factor alone (G_YELLOW ≈ 3.8em) sits BELOW a typical planet's own ceiling
 *  (2.4em), so common star kinds no longer read as bigger than their own
 *  planets at all. Giant-class stars (O_BLUE_SUPER, RED_GIANT) already clear
 *  the floor on their own factor and are unaffected — this only lifts the
 *  common/small kinds up to "unmistakably THE star". */
export function starAnchor(sectorId: number, star: SystemStar | null, bodies: SystemBody[] = []): StarAnchor {
  const rng = new SeededRng(deriveChildSeed(NS, `star:${sectorId}`));
  const xPct = 9 + rng.next01() * 5; // ~9-14%
  const yPct = 42 + rng.next01() * 8; // ~42-50%
  const factor = star ? (STAR_RADIUS_FACTOR[star.kind] ?? STAR_RADIUS_FACTOR.G_YELLOW) : STAR_RADIUS_FACTOR.G_YELLOW;
  // STAR_RADIUS_FACTOR is a canvas-pixel fraction of min(w,h); 54 is a fixed
  // em-scale constant chosen so G_YELLOW (0.07) lands at ~3.8em, matching the
  // demo's own G/K-class star.size values (RATIFIED.html: 5.5, 3.8, 6, 3.4).
  const baseSizeEm = factor * 54;
  const largestPlanetEm = bodies.length > 0 ? Math.max(...bodies.map(bodySizeEm)) : STAR_SIZE_FALLBACK_PLANET_EM;
  const floorSizeEm = largestPlanetEm * STAR_MIN_SIZE_VS_LARGEST_PLANET;
  const sizeEm = Math.round(Math.max(baseSizeEm, floorSizeEm) * 10) / 10;
  return { xPct, yPct, sizeEm };
}

/** The 4 fixed decorative orbit rings — flat z-0 chrome, never tied to a
 *  real body (RATIFIED.html L1219). */
export function decorativeRings(
  star: StarAnchor
): Array<{ xPct: number; yPct: number; wPct: number; hPct: number }> {
  return DECORATIVE_RING_RADII.map((r) => ({
    xPct: star.xPct,
    yPct: star.yPct,
    wPct: r * 1.6,
    hPct: r * 2.4,
  }));
}

/** The asteroid belt annulus — mostly off-frame by design (the "sliver"),
 *  decorative + non-clickable (RATIFIED.html L1220). */
export function beltStyle(star: StarAnchor): { xPct: number; yPct: number; wPct: number; hPct: number } {
  return { xPct: star.xPct, yPct: star.yPct, wPct: 120, hPct: 170 };
}

/** orbit_au never exceeds this in the live data (celestial_service.py's own
 *  `rng.uniform(0.2, 0.95)` ceiling, see AU_SEMI_X/Y_PCT's own citation
 *  above) — safeOrbitRadii's margin math treats it as the worst-case radius
 *  multiplier a real body/station can reach, and orbitalPosition
 *  defensively clamps to it below so a stray out-of-contract value from the
 *  network can never blow past the computed safe box either. */
export const ORBIT_AU_MAX = 0.95;

/** Real measured band geometry — WindshieldTableau.tsx's own containerRef
 *  rect (`.ssv-tableau`, 100% of `.band`) + its resolved font-size (every
 *  em value in this module, including bodySizeEm/star.sizeEm, is relative
 *  to THIS root — see bodySizeEm's own doc-comment). Threading it through
 *  as an explicit input keeps this module DOM-free/pure (per the file
 *  header) — the one caller with real DOM access measures it, everything
 *  here just does math on the numbers. */
export interface BandGeometry {
  widthPx: number;
  heightPx: number;
  /** px-per-1em at the tableau's own font-size context. */
  remPx: number;
}

/** Independent per-direction elliptical radii (%-per-orbit_au), one for
 *  each side of the star anchor.
 *
 *  T1-A (Max live-playtest): bodies were rendering partially off every edge
 *  of the flight-mode band — a body clipped off the bottom, another
 *  ("PROCEDURAL-21-6") hugging the very top edge. VERIFY-FIRST finding: the
 *  overflow was NOT vertical-only — a standalone measurement across the
 *  full orbit_au [0.2,0.95] x phase_deg [0,360) space (using the OLD
 *  symmetric AU_SEMI_X_PCT=80/AU_SEMI_Y_PCT=120 scale) showed xPct ranging
 *  -63% to +86% and yPct ranging -66% to +158% — both axes wildly outside
 *  [0,100]%, because those constants were tuned against the RATIFIED demo's
 *  own roomier, closer-to-square canvas (RATIFIED.html), not the actual
 *  flight-mode `.band` this component now renders into: a very WIDE-SHORT
 *  strip (18.5em tall — ~335px at 1440x900's resolved em-root — vs. a full
 *  ~1440px-wide band) with `overflow:hidden`, so anything outside [0,100]%
 *  on EITHER axis is silently clipped.
 *
 *  A single SYMMETRIC radius can't fix this without also destroying the
 *  "sliver" spread: the star anchors deliberately OFF-CENTER-LEFT (9-14%
 *  from the left edge — starAnchor's own doc-comment, live-playtest #4/#18)
 *  so the room actually available differs hugely per direction — almost
 *  none to the left of the star, most of the band's width to the right; a
 *  much more even split up/down. Sizing one shared radius to the TIGHTEST
 *  direction (left) would crush the whole sliver into a fraction of its
 *  intended spread; sizing it to the ROOMIEST direction (right) leaves
 *  every other edge overflowing exactly as before. Four independent radii —
 *  each maxed out for ITS OWN direction — fixes both: generous where the
 *  off-center anchor leaves room (right, and up/down are close to
 *  balanced), compressed only where the anchor itself makes it
 *  unavoidable (left). Continuous at the axes (cos=0 or sin=0 zeroes out
 *  that axis's contribution regardless of which of the two radii for it is
 *  in play), so orbitalPosition never has a seam/discontinuity switching
 *  between them. */
export interface SafeOrbitRadii {
  leftPctPerAu: number;
  rightPctPerAu: number;
  upPctPerAu: number;
  downPctPerAu: number;
  /** The safe interior box itself, so orbitalPosition can hard-clamp the
   *  FINAL position as a last-resort safety net — covers the case where
   *  the star's OWN anchor already sits inside an object's margin (e.g. a
   *  very wide `.obj` station footprint next to a tightly-left-anchored
   *  star, live T1-A proof finding): at cos(phase_deg)=0 (or sin=0) the
   *  radius contributes NOTHING to that axis, so no amount of radius-
   *  scaling alone can pull the position off the star's raw xPct/yPct —
   *  only a hard clamp on the final coordinate can. A no-op whenever the
   *  star's own anchor already clears the margin (every planet, and most
   *  stations) — see orbitalPosition's own use of this below. */
  xMinPct: number;
  xMaxPct: number;
  yMinPct: number;
  yMaxPct: number;
}

/** Computes the four safe per-direction radii for THIS star's actual
 *  anchor + THIS band's actual measured geometry, so every real body/
 *  station at orbit_au<=ORBIT_AU_MAX lands with its own full rendered
 *  footprint (centered — see the .pl/.obj/.sun/.other
 *  transform:translate(-50%,-50%) convention every sibling already uses)
 *  fully inside [0,100]% on both axes, by construction — not by post-hoc
 *  clipping. Returns 0 for a direction with no usable room at all (a
 *  pathologically tiny band) rather than a negative radius.
 *
 *  `maxObjectEmWidth`/`maxObjectEmHeight` are the largest footprint ANY
 *  object placed with these radii could render at, on each axis
 *  INDEPENDENTLY — split because a `.pl` planet disc is a fixed [0.9,2.4]em
 *  square (BODY_SIZE_EM_MAX; its `.pltag` label is position:absolute, so it
 *  escapes `.pl`'s own layout box and correctly doesn't count here), but a
 *  `.obj` station is `display:flex;flex-direction:column` with its
 *  `.objtag` LABEL as a normal-flow child — the button's own rendered WIDTH
 *  grows with the station's name length (empirically ~7.4px/char at this
 *  module's own reference remPx, live-measured T1-A proof), while its
 *  HEIGHT stays ~constant regardless of name length (the label never
 *  wraps — nothing constrains `.obj`'s own width). WindshieldTableau.tsx
 *  computes two SEPARATE SafeOrbitRadii — one sized for planets, one (with
 *  a much wider width ceiling) for stations — so a long station name
 *  doesn't needlessly compress planet placement, which needs no such
 *  margin. */
export function safeOrbitRadii(
  star: StarAnchor,
  band: BandGeometry,
  maxObjectEmWidth: number,
  maxObjectEmHeight: number = maxObjectEmWidth
): SafeOrbitRadii {
  const halfWidthPx = (maxObjectEmWidth / 2) * band.remPx;
  const halfHeightPx = (maxObjectEmHeight / 2) * band.remPx;
  const marginXPct = band.widthPx > 0 ? (halfWidthPx / band.widthPx) * 100 : 100;
  const marginYPct = band.heightPx > 0 ? (halfHeightPx / band.heightPx) * 100 : 100;
  const room = (roomPct: number) => Math.max(0, roomPct) / ORBIT_AU_MAX;
  // Clamp margins that exceed half the box (a pathologically small band, or
  // a footprint wider than the box itself) so xMin<=xMax/yMin<=yMax always
  // holds -- an inverted range would make the final Math.min/Math.max clamp
  // in orbitalPosition silently pick the wrong bound.
  const safeMarginX = Math.min(marginXPct, 50);
  const safeMarginY = Math.min(marginYPct, 50);
  return {
    leftPctPerAu: room(star.xPct - marginXPct),
    rightPctPerAu: room(100 - marginXPct - star.xPct),
    upPctPerAu: room(star.yPct - marginYPct),
    downPctPerAu: room(100 - marginYPct - star.yPct),
    xMinPct: safeMarginX,
    xMaxPct: 100 - safeMarginX,
    yMinPct: safeMarginY,
    yMaxPct: 100 - safeMarginY,
  };
}

/** Real orbit_au + phase_deg → a STATIC %-position on the star's orbital
 *  plane. No `t` term — zero system-level animation at rest (Max #4).
 *
 *  Without `safeRadii` (decorative callers, and any caller mid-mount before
 *  a real band has been measured), this is byte-identical to the original
 *  symmetric AU_SEMI_X_PCT/AU_SEMI_Y_PCT math — unchanged so decorativeRings/
 *  beltStyle's own visual-consistency-with-real-bodies intent (their own
 *  doc-comments) and every pre-T1-A test stay exactly as they were. With
 *  `safeRadii` (WindshieldTableau.tsx's real bodyPosition/stationPosition
 *  calls, once it has measured its own container), the per-quadrant radii
 *  replace the symmetric ones and orbit_au is defensively clamped to
 *  ORBIT_AU_MAX — see safeOrbitRadii's own doc-comment for why. */
export function orbitalPosition(
  star: StarAnchor,
  orbitAu: number,
  phaseDeg: number,
  safeRadii?: SafeOrbitRadii
): PctPoint {
  const rad = (phaseDeg * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  if (!safeRadii) {
    const rx = orbitAu * AU_SEMI_X_PCT;
    const ry = orbitAu * AU_SEMI_Y_PCT;
    return { xPct: star.xPct + cos * rx, yPct: star.yPct + sin * ry };
  }
  const au = Math.min(Math.abs(orbitAu), ORBIT_AU_MAX);
  const rx = au * (cos >= 0 ? safeRadii.rightPctPerAu : safeRadii.leftPctPerAu);
  const ry = au * (sin >= 0 ? safeRadii.downPctPerAu : safeRadii.upPctPerAu);
  // Final hard clamp — see SafeOrbitRadii's own xMinPct/xMaxPct/yMinPct/
  // yMaxPct doc-comment for why this is needed even after the per-quadrant
  // radius scaling above (the star's own raw anchor can itself sit inside
  // a wide object's margin at cos=0/sin=0, where the radius contributes
  // nothing). A no-op whenever the un-clamped result already lands inside
  // the safe box, which is the common case for every planet and most
  // stations.
  const xPct = Math.min(Math.max(star.xPct + cos * rx, safeRadii.xMinPct), safeRadii.xMaxPct);
  const yPct = Math.min(Math.max(star.yPct + sin * ry, safeRadii.yMinPct), safeRadii.yMaxPct);
  return { xPct, yPct };
}

export function bodyPosition(star: StarAnchor, body: SystemBody, safeRadii?: SafeOrbitRadii): PctPoint {
  return orbitalPosition(star, body.orbit_au, body.phase_deg, safeRadii);
}

/** bodySizeEm's own ceiling (em) — named so safeOrbitRadii below (and any
 *  other caller needing "the biggest a body disc can possibly render") has
 *  one source of truth instead of a second hardcoded 2.4. */
export const BODY_SIZE_EM_MAX = 2.4;

/** A body's own rendered disc size (em) — single source of truth shared by
 *  WindshieldTableau.tsx's `.pl` sizing AND moonOrbits' radius scaling below
 *  (Max addendum, live-playtest #9: moon-orbit DETACHMENT was a planet-size-
 *  blind radius, unrelated to how big the parent disc actually renders). */
export function bodySizeEm(body: SystemBody): number {
  return Math.min(BODY_SIZE_EM_MAX, Math.max(0.9, 0.55 + body.size_class * 0.28));
}

export function stationPosition(star: StarAnchor, station: SystemStation, safeRadii?: SafeOrbitRadii): PctPoint {
  return orbitalPosition(star, station.orbit_au, station.phase_deg, safeRadii);
}

/** One child-orbit's CSS-animation parameters. Rendered as a small rotating
 *  wrapper (transform-origin at the parent's center, translateX(radiusEm))
 *  so the ANIMATION is pure CSS and dies for free under
 *  prefers-reduced-motion (solar-system-viewscreen.css). `clockwise` is the
 *  SAME value for every moon of one body (a co-rotating family — see
 *  moonOrbits below); `sizeEm` is the individual moon-dot's own diameter. */
export interface MoonOrbit {
  radiusEm: number;
  durationS: number;
  startDeg: number;
  clockwise: boolean;
  sizeEm: number;
}

/** Moon-dot diameter band (em) — the "~2-5px" range Max asked for
 *  (live-playtest #17), expressed against the codebase's nominal 16px em
 *  root (no ancestor of `.ssv-tableau` sets its own font-size — see
 *  index.css's html/body rule — so 1em there resolves against whatever the
 *  `.stage`/`.game-container` em-root computes, the same convention every
 *  other em value in this module already relies on). */
export const MOON_DOT_MIN_EM = 0.18;
export const MOON_DOT_MAX_EM = 0.32;

/** Minimum radial gap (em) between two consecutive moon orbit tracks of the
 *  SAME family — must clear MOON_DOT_MAX_EM (the largest possible dot) with
 *  margin so no two tracks ever read as touching/competing (Max: "at
 *  varying non-competing distances"). Chosen so even the worst-case
 *  per-moon jitter below can't erode the gap under MOON_DOT_MAX_EM. */
const MOON_TRACK_STAGGER_EM = 0.55;
const MOON_TRACK_JITTER_MAX_EM = 0.1;

/** Max's refinement (5a): system-level bodies stay fixed, but a body's own
 *  children (moons) keep slow, local, parent-anchored orbital motion. Reuses
 *  the SAME "moons: number (count only)" field + per-index seeding idiom the
 *  live canvas already uses (SolarSystemViewscreen.tsx's moonRng) — there is
 *  no richer moon data model (individual moon ids/positions) yet, so this is
 *  the full extent of "if the data model HAS parent-child bodies"; stations
 *  carry no such field today, so they get no child-orbit layer (forward-
 *  looking: any future satellite-count field on SystemStation can attach
 *  here the same way, unchanged shape).
 *
 * Max addendum, live-playtest #9: the first cut read as "erratic wandering
 * stars" rather than moons — two concrete numeric defects, both fixed here
 * ("slow, subtle, parent-anchored"): SPEED (14-24s/lap is fast enough to
 * visibly race around the disc — now 40-90s) and DETACHMENT (the old radius
 * was a flat 1.5-2.1em regardless of the parent's own rendered size, so a
 * SMALL planet's moon sat 3-4x its disc radius away and read as a free-
 * floating star — now scaled off bodySizeEm() so every moon sits ~0.6-1.2
 * planet-radii OUTSIDE its OWN parent's edge, whatever that parent's size).
 * The wrapper-rotates/dot-offsets CSS-only mechanism itself (no transition,
 * no per-frame JS writes — solar-system-viewscreen.css's `.moon-orbit`/
 * `.moon-dot`) was already structurally correct; only these two numbers
 * needed retuning.
 *
 * Max addendum, live-playtest #17: "varied in size, all rotate the SAME WAY
 * around a planet, at varying non-competing distances" — the previous cut
 * drew `clockwise` PER MOON (independently random), so one planet's moons
 * could spin in opposite directions, and its per-moon radius term
 * (`edgeFactor`, drawn independently per moon in [0.6, 1.2] planet-radii)
 * could make a LATER moon land closer in than an EARLIER one whenever
 * planetRadiusEm was large enough for that draw-to-draw swing to outweigh
 * the old flat +0.4em stagger — no gap guarantee. Fixed here: direction is
 * drawn ONCE per family (deterministic per sectorId+body.slot, so it can
 * still differ planet-to-planet) and applied to every moon; radius is now
 * base + m*MOON_TRACK_STAGGER_EM + a small bounded per-moon jitter, so
 * consecutive tracks are ALWAYS separated by at least
 * MOON_TRACK_STAGGER_EM - MOON_TRACK_JITTER_MAX_EM (0.45em), comfortably
 * above MOON_DOT_MAX_EM (0.32em) — no two orbital tracks can ever compete. */
export function moonOrbits(sectorId: number, body: SystemBody): MoonOrbit[] {
  // One direction for the WHOLE family — independent seed stream from the
  // per-moon draws below so adding/removing moons never perturbs it.
  const familyRng = new SeededRng(deriveChildSeed(NS, `moon-family:${sectorId}:${body.slot}`));
  const clockwise = familyRng.next01() < 0.5;

  const rng = new SeededRng(deriveChildSeed(NS, `moons:${sectorId}:${body.slot}`));
  const planetRadiusEm = bodySizeEm(body) / 2;
  const baseOffset = 0.6 + rng.next01() * 0.3; // 0.6-0.9 planet-radii OUTSIDE the edge for the innermost moon
  const out: MoonOrbit[] = [];
  for (let m = 0; m < body.moons; m++) {
    const jitterEm = rng.next01() * MOON_TRACK_JITTER_MAX_EM;
    const radiusEm = planetRadiusEm * (1 + baseOffset) + m * MOON_TRACK_STAGGER_EM + jitterEm;
    out.push({
      radiusEm,
      durationS: 40 + rng.next01() * 50, // one revolution ~40-90s — slow, subtle
      startDeg: rng.next01() * 360,
      clockwise,
      sizeEm: MOON_DOT_MIN_EM + rng.next01() * (MOON_DOT_MAX_EM - MOON_DOT_MIN_EM),
    });
  }
  return out;
}

/** Non-orbital objects (wrecks, formations) — not gravitationally bound, so
 *  they get a stable seeded scatter position instead of an orbit (mirrors
 *  SolarSystemViewscreen.tsx's scanContactPosition idiom, in %-space). */
export function scanPosition(id: string): PctPoint {
  const rng = new SeededRng(deriveChildSeed(`${NS}-scan`, id));
  return { xPct: 8 + rng.next01() * 84, yPct: 10 + rng.next01() * 78 };
}

/** Other ships/pirates — static seeded scatter (the demo's `.other` glyphs
 *  carry no transition/animation; the player's OWN ship is the only mover). */
export function otherPresencePosition(id: string): PctPoint {
  const rng = new SeededRng(deriveChildSeed(`${NS}-presence`, id));
  return { xPct: 8 + rng.next01() * 84, yPct: 10 + rng.next01() * 78 };
}

/** The player's own ship's RESTING anchor when there is no better seed (no
 *  last-docked/landed host to emerge from) — a fresh arrival into the
 *  sector. Purely cosmetic, matching the existing "no real intrasystem
 *  position model" precedent (SolarSystemViewscreen.tsx's selfBaseRef). */
export function selfRestingAnchor(sectorId: number): PctPoint {
  const rng = new SeededRng(deriveChildSeed(`${NS}-self`, String(sectorId)));
  return { xPct: 20 + rng.next01() * 55, yPct: 20 + rng.next01() * 55 };
}

/** Heading in degrees (CSS `--hdg`) from a previous position toward a new
 *  one, for the `.shipmk` rotate(var(--hdg)) transform. */
export function headingDeg(from: PctPoint, to: PctPoint): number {
  if (from.xPct === to.xPct && from.yPct === to.yPct) return 0;
  return (Math.atan2(to.yPct - from.yPct, to.xPct - from.xPct) * 180) / Math.PI;
}

/** A single hazard band's arc geometry (fraction-of-orbit radius + sweep),
 *  independent of any per-object x/y — the REAL data model represents
 *  nebula/debris as system-wide fields (SystemSnapshot.nebula.{hue,density},
 *  .debris.{inner_au,outer_au,hue}), not discrete positioned bodies like the
 *  demo's `sys.bodies` nebula/radcloud entries. This ports the demo's VISUAL
 *  idiom (blurred SVG arcs along the orbital plane, WO composition step 2:
 *  "not rings") while feeding it from the real system-wide fields, mirroring
 *  the live canvas's own drawHazardBandArc anchoring (same orbital plane as
 *  the orbit rings, seeded band count/position). */
export interface HazardArc {
  rFrac: number;
  startDeg: number;
  sweepDeg: number;
}

/** Nebula haze — 2-3 seeded partial arcs at varying radii (mirrors the live
 *  canvas's `bandCount = 2 + floor(rng()*2)`, RATIFIED SolarSystemViewscreen
 *  drawScene). */
export function nebulaArcs(sectorId: number): HazardArc[] {
  const rng = new SeededRng(deriveChildSeed(NS, `nebula-arc:${sectorId}`));
  const count = 2 + Math.floor(rng.next01() * 2);
  const arcs: HazardArc[] = [];
  for (let i = 0; i < count; i++) {
    arcs.push({
      rFrac: 0.35 + rng.next01() * 0.55,
      startDeg: rng.next01() * 360,
      sweepDeg: 90 + rng.next01() * 126,
    });
  }
  return arcs;
}

/** Collision-debris ring — a single near-complete band at the ring's
 *  midpoint radius (system.debris.{inner_au,outer_au}). */
export function debrisArc(debris: { inner_au: number; outer_au: number }): HazardArc {
  return { rFrac: (debris.inner_au + debris.outer_au) / 2, startDeg: 0, sweepDeg: 350 };
}
