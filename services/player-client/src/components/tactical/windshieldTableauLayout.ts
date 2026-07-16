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

/** T0-2 (Max: "your pick, knock it out" — orbit-line view). The tilt ratio
 *  (ry/rx) every per-body orbit ellipse below uses, so the new individual
 *  orbit lines read as the SAME orbital-plane as the rest of this module's
 *  %-space geometry — this is the exact ratio the RETIRED decorativeRings
 *  used (w=r*1.6%, h=r*2.4%, i.e. semi-axes r*0.8/r*1.2 → ratio 1.5), which
 *  is itself just AU_SEMI_Y_PCT/AU_SEMI_X_PCT (120/80 = 1.5) — one shared
 *  constant instead of two separately-tuned magic ratios that happen to
 *  agree. */
export const ORBIT_TILT_RATIO = AU_SEMI_Y_PCT / AU_SEMI_X_PCT;

/** A single body/station's own orbit ellipse — centered on the star, tilted
 *  at ORBIT_TILT_RATIO, sized so its path passes EXACTLY through `bodyPos`
 *  (the body's own already-computed rendered position — T0-1's fan/rank
 *  positioning is completely untouched by this; the ellipse is derived
 *  FROM the position, never the other way around). Solving
 *  `(dx/rx)^2 + (dy/ry)^2 = 1` with `ry = ORBIT_TILT_RATIO*rx` for `rx`:
 *  `rx = sqrt(dx^2 + (dy/ORBIT_TILT_RATIO)^2)`.
 *
 *  REPLACES the old generic, cosmetic-only decorativeRings (4 fixed rings,
 *  never tied to a real body) — Max's own ask: "every planet/station rides
 *  its own orbit line, and its spot on that line is where we are on the
 *  orbital plane" — a real per-body ellipse the body visibly sits ON, not
 *  decoration behind it. Returns `null` for the degenerate case (`bodyPos`
 *  exactly AT the star's own anchor — never observed in practice, since
 *  the safe-radii margin always keeps real bodies clear of the star's own
 *  disc, but a zero-radius ellipse isn't renderable either way). */
export function bodyOrbitEllipse(star: StarAnchor, bodyPos: PctPoint): { cxPct: number; cyPct: number; rxPct: number; ryPct: number } | null {
  const dx = bodyPos.xPct - star.xPct;
  const dy = bodyPos.yPct - star.yPct;
  if (dx === 0 && dy === 0) return null;
  const rxPct = Math.sqrt(dx * dx + (dy / ORBIT_TILT_RATIO) * (dy / ORBIT_TILT_RATIO));
  const ryPct = ORBIT_TILT_RATIO * rxPct;
  return { cxPct: star.xPct, cyPct: star.yPct, rxPct, ryPct };
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

/** orbit_au never goes BELOW this either (celestial_service.py's own
 *  `rng.uniform(0.2, 0.95)` floor) — orbitalPosition's T0-1 X-axis model
 *  (below) normalizes a body's orbit_au into this [ORBIT_AU_MIN,
 *  ORBIT_AU_MAX] range to drive its horizontal "how far out" position. */
export const ORBIT_AU_MIN = 0.2;

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
  /** Retained for its own correctness (still asserted directly by
   *  windshieldTableauLayout.test.ts) but NO LONGER what drives a body's X
   *  position — see orbitalPosition's own T0-1 doc-comment for why a
   *  cos-sign-branched left/right radius pair collapses bodies onto the
   *  star whenever every body's phase lands in the left hemisphere while
   *  the star sits far-left (leftPctPerAu≈0 by construction there). */
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

/** T0-1 (Max live-catch, sector 1): the fraction of a body's primary X
 *  spread (orbitT * rightPctPerAu*ORBIT_AU_MAX below) that phase_deg is
 *  ALSO allowed to contribute, as a small SECONDARY horizontal wiggle —
 *  Max's ruling in his own words: "primarily vertical + secondary
 *  horizontal, but must NEVER zero out the horizontal spread." Deliberately
 *  ONE-SIDED (see orbitalPosition's `(cos+1)/2` remap below, always >=0) —
 *  a signed +/- wiggle could subtract enough from a body at the smallest
 *  orbit_au (orbitT≈0) to push it PAST the star itself when phase lands in
 *  the left hemisphere, reopening the exact collapse this WO exists to
 *  close. One-sided keeps every body at or beyond its own orbit_au's
 *  baseline "how far out" position, never behind it. */
const X_SECONDARY_WIGGLE_FRACTION = 0.15;

/** Real orbit_au + phase_deg → a STATIC %-position on the star's orbital
 *  plane. No `t` term — zero system-level animation at rest (Max #4).
 *
 *  Without `safeRadii` (decorative callers, and any caller mid-mount before
 *  a real band has been measured), this is byte-identical to the original
 *  symmetric AU_SEMI_X_PCT/AU_SEMI_Y_PCT math — unchanged so beltStyle's own
 *  visual-consistency-with-real-bodies intent (its own doc-comment) and
 *  every pre-T1-A test stay exactly as they were.
 *
 *  With `safeRadii`, Y stays the T1-A mechanism unchanged (phase-DOMINANT,
 *  orbit_au-scaled: `sin(phase) * au * up/downPctPerAu` — Max: "vertical
 *  spread is fine"). X is REDESIGNED (T0-1, live-caught at sector 1): the
 *  old cos-sign-branched left/right radius pair put phase in charge of X
 *  too, and Max's own ruling anchors the star FAR-LEFT permanently (chosen
 *  deliberately over a centered orrery — not up for renegotiation here), so
 *  `leftPctPerAu` is essentially always ~0 by construction. Whenever every
 *  body in a system happened to share a left-hemisphere phase (sector 1's
 *  live data: all 6 bodies), the old formula collapsed every one of them
 *  onto the star's own xPct regardless of how different their orbit_au
 *  was — 6 distinct orbits read as "one planet" in a 96px-wide pile.
 *
 *  X is now driven PRIMARILY by orbit_au itself — a "right-sweeping fan":
 *  further out = further right, monotonic, phase-independent — using the
 *  SAME proven-safe rightward room the star's far-left anchor always has
 *  (`rightPctPerAu * ORBIT_AU_MAX`, i.e. safeOrbitRadii's own T1-A-proven
 *  in-band ceiling), so two different orbit_au values can never land at the
 *  same X regardless of phase. Phase only adds the small, ONE-SIDED
 *  secondary wiggle above — real "along the orbit" horizontal texture that
 *  can never zero the primary spread back out. `leftPctPerAu` and the
 *  cos-sign branch are gone from the X formula entirely (kept on
 *  SafeOrbitRadii itself only for its own correctness/tests — see that
 *  interface's own doc-comment). orbit_au is still defensively clamped to
 *  [ORBIT_AU_MIN, ORBIT_AU_MAX] — see those constants' own doc-comments —
 *  and the SAME final xMinPct/xMaxPct/yMinPct/yMaxPct hard clamp from T1-A
 *  still backstops both axes. */
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
  const au = Math.min(Math.max(Math.abs(orbitAu), ORBIT_AU_MIN), ORBIT_AU_MAX);
  // X: primary term is a monotonic function of orbit_au alone (the
  // right-sweeping fan) + a small one-sided phase wiggle. Y: unchanged
  // T1-A mechanism (phase-dominant, orbit_au-scaled).
  const orbitT = (au - ORBIT_AU_MIN) / (ORBIT_AU_MAX - ORBIT_AU_MIN);
  const xSpreadPct = safeRadii.rightPctPerAu * ORBIT_AU_MAX;
  const xWigglePct = ((cos + 1) / 2) * xSpreadPct * X_SECONDARY_WIGGLE_FRACTION;
  const ry = au * (sin >= 0 ? safeRadii.downPctPerAu : safeRadii.upPctPerAu);
  // Final hard clamp — see SafeOrbitRadii's own xMinPct/xMaxPct/yMinPct/
  // yMaxPct doc-comment for why this is needed even after the primary/
  // secondary terms above (the star's own raw anchor can itself sit inside
  // a wide object's margin, where neither term guarantees clearance on its
  // own). A no-op whenever the un-clamped result already lands inside the
  // safe box, which is the common case for every planet and most stations.
  const xPct = Math.min(Math.max(star.xPct + orbitT * xSpreadPct + xWigglePct, safeRadii.xMinPct), safeRadii.xMaxPct);
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

/** WO-UI-PLTAG-CLAMP (Max fly-by catch, sector 68: "Pollux" clipped ~1.7px
 *  past the band's right edge): `.pl`/`.other`/star-tag's shared `.pltag`
 *  label is `position:absolute` (escapes its anchor's own layout box BY
 *  DESIGN — see PLANET_FOOTPRINT_EM_MAX's own doc-comment, that's what
 *  keeps a body's disc size independent of its name length), so a body
 *  positioned near a band edge can have its DISC fully in-band (T1-A) while
 *  its CENTERED label still crosses the boundary once the label is wider
 *  than the disc. `pltagLabelHalfWidthEm` estimates that width so
 *  `labelEdgeLean` below can decide whether the label needs to lean off-
 *  center to stay in-band — WITHOUT ever touching the anchor's own
 *  xPct/yPct (T1-A's body-position math is a separate, proven system; this
 *  WO's own report is the record of why that stays untouched).
 *
 *  Base/per-char constants are live-measured (Playwright, 1440x900 flight-
 *  mode band, this WO's own report) against `.pltag`'s REAL rendered width
 *  for 5 real body names spanning 4-50 chars (4/10/16/21/50), same
 *  empirically-grounded-ceiling idiom as STATION_FOOTPRINT_EM_WIDTH_MAX's
 *  own doc-comment — deliberately biased slightly HIGH (never underestimated
 *  the 5 measured points; worst residual was the 16-char point, ~14px
 *  overestimate) so a caller triggers the lean a touch early rather than
 *  late. 50 chars is the ADR-0073 rename ceiling (gameserver
 *  planets.py:_rename_planet_by_discoverer, "50 characters or fewer",
 *  server-validated) — a REAL reachable width for a body's name
 *  (gameserver celestial_service.py:389 confirms real planet bodies use
 *  `planet.display_name`, the same custom_name-aware field the rename
 *  endpoint writes), not a hypothetical ceiling. */
export const PLTAG_LABEL_BASE_EM = 0.8;
export const PLTAG_LABEL_PER_CHAR_EM = 0.43;

export function pltagLabelHalfWidthEm(name: string): number {
  return (PLTAG_LABEL_BASE_EM + PLTAG_LABEL_PER_CHAR_EM * name.length) / 2;
}

export type LabelEdgeLean = 'left' | 'right' | null;

/** Given an anchor's xPct (a `.pl`/`.other`/star's OWN center, unchanged)
 *  and its label's estimated half-width, decides which way (if any) the
 *  `.pltag` needs to lean to stay fully inside the band's [0,100]% width.
 *  `band` undefined (mid-mount, before real geometry is measured — mirrors
 *  every other T1-A safety net's own `!safeRadii`/no-op-before-band-
 *  measured convention) or a non-positive width both return `null` (no
 *  lean) rather than guessing. */
export function labelEdgeLean(
  xPct: number,
  labelHalfWidthEm: number,
  band?: BandGeometry
): LabelEdgeLean {
  if (!band || band.widthPx <= 0) return null;
  const halfWidthPx = labelHalfWidthEm * band.remPx;
  const xPx = (xPct / 100) * band.widthPx;
  if (xPx + halfWidthPx > band.widthPx) return 'right';
  if (xPx - halfWidthPx < 0) return 'left';
  return null;
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

/** Other ships/pirates — seeded scatter anchor for a contact glyph. Used as
 *  a fallback waypoint when the system has fewer than two real docks; live
 *  traffic uses `otherShipFlightPose` between planet/station positions. */
export function otherPresencePosition(id: string): PctPoint {
  const rng = new SeededRng(deriveChildSeed(`${NS}-presence`, id));
  return { xPct: 8 + rng.next01() * 84, yPct: 10 + rng.next01() * 78 };
}

/**
 * Contact flight profile — SAME methodical procedure as the player's
 * `.shipmk` local travel (WindshieldTableau TRAVEL_* constants):
 *   orient → accelerate (burn) → coast → brake-turn (RCS flip) →
 *   decelerate (burn) → final-orient → dwell, then the next leg.
 * Straight-line eased glide between waypoints (no Lissajous curves).
 * Timings must stay in lockstep with solar-system-viewscreen.css's
 * `.shipmk.travel-*` 6.4s position duration.
 */
export const OTHER_FLIGHT_ORIENT_MS = 1000;
export const OTHER_FLIGHT_ACCEL_MS = 1800;
export const OTHER_FLIGHT_COAST_MS = 1100;
export const OTHER_FLIGHT_FLIP_MS = 1300;
export const OTHER_FLIGHT_DECEL_MS = 2200;
export const OTHER_FLIGHT_SETTLE_MS = 800;
export const OTHER_FLIGHT_MOVE_MS =
  OTHER_FLIGHT_ACCEL_MS + OTHER_FLIGHT_COAST_MS + OTHER_FLIGHT_FLIP_MS + OTHER_FLIGHT_DECEL_MS;

export type OtherFlightPhase =
  | 'idle'
  | 'orienting'
  | 'accelerating'
  | 'gliding'
  | 'brake-turn'
  | 'braking'
  | 'final-orient';

export interface OtherShipFlightPose extends PctPoint {
  headingDeg: number;
  phase: OtherFlightPhase;
  burning: boolean;
}

function smoothstep01(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
}

function shortestAngleDeltaDeg(from: number, to: number): number {
  return ((to - from + 540) % 360) - 180;
}

function lerpPct(a: PctPoint, b: PctPoint, t: number): PctPoint {
  return {
    xPct: a.xPct + (b.xPct - a.xPct) * t,
    yPct: a.yPct + (b.yPct - a.yPct) * t,
  };
}

/** Build a ≥2 waypoint pool from real docks, falling back to seeded scatter. */
export type ContactDock = PctPoint & {
  /** Destination realism bucket (Max 2026-07-16). */
  bucket?: 'habitable' | 'barren' | 'outbound';
};

export function otherShipWaypoints(id: string, docks: PctPoint[]): PctPoint[] {
  const uniq: PctPoint[] = [];
  for (const d of docks) {
    if (!uniq.some((u) => Math.hypot(u.xPct - d.xPct, u.yPct - d.yPct) < 1.5)) {
      uniq.push(d);
    }
  }
  if (uniq.length >= 2) return uniq;
  const rng = new SeededRng(deriveChildSeed(`${NS}-presence-waypoints`, id));
  const base = otherPresencePosition(id);
  const extras: PctPoint[] = [base];
  while (extras.length < 3) {
    extras.push({
      xPct: 10 + rng.next01() * 80,
      yPct: 12 + rng.next01() * 76,
    });
  }
  return extras;
}

function outboundRimDocks(id: string): ContactDock[] {
  const rng = new SeededRng(deriveChildSeed(`${NS}-outbound`, id));
  const pts: ContactDock[] = [];
  for (let i = 0; i < 3; i += 1) {
    const edge = Math.floor(rng.next01() * 4);
    let xPct: number;
    let yPct: number;
    if (edge === 0) { xPct = 4 + rng.next01() * 6; yPct = 15 + rng.next01() * 70; }
    else if (edge === 1) { xPct = 90 + rng.next01() * 6; yPct = 15 + rng.next01() * 70; }
    else if (edge === 2) { xPct = 15 + rng.next01() * 70; yPct = 6 + rng.next01() * 8; }
    else { xPct = 15 + rng.next01() * 70; yPct = 86 + rng.next01() * 8; }
    pts.push({ xPct, yPct, bucket: 'outbound' });
  }
  return pts;
}

function destinationWeights(opts?: {
  archetype?: string | null;
  activity?: string | null;
  mission?: string | null;
}): Record<'habitable' | 'outbound' | 'barren', number> {
  const arch = (opts?.archetype || '').toUpperCase();
  const act = (opts?.activity || '').toUpperCase();
  const miss = (opts?.mission || '').toLowerCase();
  if (miss === 'science' || arch === 'RESEARCHER') {
    return { habitable: 0.40, outbound: 0.20, barren: 0.40 };
  }
  if (miss === 'colonist') return { habitable: 0.72, outbound: 0.20, barren: 0.08 };
  if (miss === 'commerce' || arch === 'TRADER') {
    return { habitable: 0.68, outbound: 0.22, barren: 0.10 };
  }
  if (act === 'PATROL' || arch === 'LAW_ENFORCEMENT') {
    return { habitable: 0.55, outbound: 0.30, barren: 0.15 };
  }
  if (arch === 'HOSTILE_RAIDER') return { habitable: 0.45, outbound: 0.35, barren: 0.20 };
  return { habitable: 0.60, outbound: 0.20, barren: 0.20 };
}

function pickWeightedDest(
  id: string,
  leg: number,
  from: PctPoint,
  docks: ContactDock[],
  opts?: { archetype?: string | null; activity?: string | null; mission?: string | null },
): PctPoint {
  const pools: Record<'habitable' | 'outbound' | 'barren', ContactDock[]> = {
    habitable: [],
    barren: [],
    outbound: outboundRimDocks(`${id}:rim`),
  };
  for (const d of docks) {
    const b = d.bucket || 'habitable';
    pools[b].push(d);
  }
  // Untagged docks that aren't rim → treat as habitable (stations / unknown)
  if (pools.habitable.length === 0 && docks.length > 0) {
    pools.habitable = docks.map((d) => ({ ...d, bucket: 'habitable' as const }));
  }

  const weights = destinationWeights(opts);
  const available = (Object.keys(pools) as Array<keyof typeof pools>).filter((k) => pools[k].length > 0);
  if (available.length === 0) {
    return otherShipWaypoints(id, docks)[0] || { xPct: 50, yPct: 50 };
  }
  const total = available.reduce((s, k) => s + weights[k], 0) || available.length;
  const rng = new SeededRng(deriveChildSeed(`${NS}-dest-pick`, `${id}:${leg}`));
  let roll = rng.next01() * total;
  let bucket: keyof typeof pools = available[0];
  for (const k of available) {
    roll -= weights[k];
    if (roll <= 0) { bucket = k; break; }
  }
  const optsList = pools[bucket];
  const ranked = [...optsList].sort(
    (a, b) => Math.hypot(b.xPct - from.xPct, b.yPct - from.yPct)
      - Math.hypot(a.xPct - from.xPct, a.yPct - from.yPct),
  );
  const top = ranked.slice(0, Math.max(1, Math.ceil(ranked.length / 2)));
  return top[Math.floor(rng.next01() * top.length)];
}

/** Cosmetic contact pose — mirrors the player's turn/burn/flip/brake legs. */
export function otherShipFlightPose(
  id: string,
  tSec: number,
  docks: ContactDock[],
  opts?: {
    archetype?: string | null;
    activity?: string | null;
    mission?: string | null;
    bandAspect?: number;
  },
): OtherShipFlightPose {
  const rng = new SeededRng(deriveChildSeed(`${NS}-presence-flight`, id));
  const bandAspect = opts?.bandAspect ?? 1;
  const arch = (opts?.archetype || '').toUpperCase();
  const act = (opts?.activity || '').toUpperCase();
  const isBusy =
    act === 'PATROL' ||
    act === 'COMMUTE' ||
    act === 'WORK_STATION' ||
    arch === 'LAW_ENFORCEMENT' ||
    arch === 'HOSTILE_RAIDER';

  const phaseOffsetMs = Math.floor(rng.next01() * 120_000);
  const dwellMs = isBusy
    ? 1800 + Math.floor(rng.next01() * 3200)
    : 4500 + Math.floor(rng.next01() * 7000);
  const legMs =
    OTHER_FLIGHT_ORIENT_MS + OTHER_FLIGHT_MOVE_MS + OTHER_FLIGHT_SETTLE_MS + dwellMs;

  const tMs = Math.max(0, tSec * 1000) + phaseOffsetMs;
  const leg = Math.floor(tMs / legMs);
  const u = tMs - leg * legMs;

  // Walk weighted destinations per leg so traders prefer docks/habitable worlds.
  let from: PctPoint = otherPresencePosition(id);
  let to = pickWeightedDest(id, 0, from, docks, opts);
  let prev = from;
  for (let i = 1; i <= leg; i += 1) {
    prev = from;
    from = to;
    to = pickWeightedDest(id, i, from, docks, opts);
  }

  const prograde = headingDeg(from, to, bandAspect);
  const retrograde = prograde + 180;
  const faceArrival = prograde + 360;
  const parkedHdg = headingDeg(prev, from, bandAspect);

  const moveStart = OTHER_FLIGHT_ORIENT_MS;
  const accelEnd = moveStart + OTHER_FLIGHT_ACCEL_MS;
  const coastEnd = accelEnd + OTHER_FLIGHT_COAST_MS;
  const flipEnd = coastEnd + OTHER_FLIGHT_FLIP_MS;
  const moveEnd = moveStart + OTHER_FLIGHT_MOVE_MS;
  const settleEnd = moveEnd + OTHER_FLIGHT_SETTLE_MS;

  if (u < moveStart) {
    const t = u / OTHER_FLIGHT_ORIENT_MS;
    const hdg = parkedHdg + shortestAngleDeltaDeg(parkedHdg, prograde) * smoothstep01(t);
    return { ...from, headingDeg: hdg, phase: 'orienting', burning: false };
  }

  if (u < moveEnd) {
    const p = (u - moveStart) / OTHER_FLIGHT_MOVE_MS;
    const pos = lerpPct(from, to, smoothstep01(p));
    if (u < accelEnd) {
      return { ...pos, headingDeg: prograde, phase: 'accelerating', burning: true };
    }
    if (u < coastEnd) {
      return { ...pos, headingDeg: prograde, phase: 'gliding', burning: false };
    }
    if (u < flipEnd) {
      const ft = (u - coastEnd) / OTHER_FLIGHT_FLIP_MS;
      const continuous = prograde + 180 * smoothstep01(ft);
      return { ...pos, headingDeg: continuous, phase: 'brake-turn', burning: false };
    }
    return { ...pos, headingDeg: retrograde, phase: 'braking', burning: true };
  }

  if (u < settleEnd) {
    const t = (u - moveEnd) / OTHER_FLIGHT_SETTLE_MS;
    const hdg = retrograde + (faceArrival - retrograde) * smoothstep01(t);
    return { ...to, headingDeg: hdg, phase: 'final-orient', burning: false };
  }

  return { ...to, headingDeg: faceArrival, phase: 'idle', burning: false };
}

/** @deprecated Use otherShipFlightPose — kept for any stray import during cutover. */
export function otherShipPose(
  id: string,
  tSec: number,
  opts?: {
    archetype?: string | null;
    activity?: string | null;
    bandAspect?: number;
  },
): OtherShipFlightPose {
  return otherShipFlightPose(id, tSec, [], opts);
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
 *  one, for the `.shipmk` rotate(var(--hdg)) transform.
 *
 *  FIX B (Max live-playtest): `xPct`/`yPct` are %-of-width and %-of-height
 *  respectively — NOT interchangeable px units — so `atan2` on the raw %
 *  deltas only gives the correct ON-SCREEN angle if the container happens
 *  to be SQUARE. The flight-mode band is ~4.3:1 wide-short (~1440x335px),
 *  so a %-space delta that looks "diagonal" is actually much steeper on
 *  screen (the same %-of-height delta covers far fewer real px than the
 *  same %-of-width delta) — the ship pointed too vertical relative to its
 *  true visual travel direction. `bandAspect` (heightPx/widthPx, defaults
 *  to 1 = the old square-container assumption, for callers without real
 *  geometry) rescales the y-term back into the same px-equivalent units as
 *  x before the atan2, so the reported heading matches what the ship
 *  visually does on screen. */
export function headingDeg(from: PctPoint, to: PctPoint, bandAspect = 1): number {
  if (from.xPct === to.xPct && from.yPct === to.yPct) return 0;
  const dxPct = to.xPct - from.xPct;
  const dyPct = to.yPct - from.yPct;
  return (Math.atan2(dyPct * bandAspect, dxPct) * 180) / Math.PI;
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
