import React from 'react';
import { deriveIspPose, ispPhaseToTravelClass, type IspPose } from '../../services/intrasystemFlight';
import type { HitMeta, ShipPresence, SystemBody, SystemStation } from './SolarSystemViewscreen';
import {
  AU_SEMI_X_PCT,
  AU_SEMI_Y_PCT,
  BODY_SIZE_EM_MAX,
  bodyOrbitEllipse,
  bodyPosition,
  bodySizeEm,
  otherPresencePosition,
  otherShipFlightPose,
  safeOrbitRadii,
  starAnchor,
  stationPosition,
  type BandGeometry,
  type ContactDock,
  type HazardArc,
  type PctPoint,
  type StarAnchor,
} from './windshieldTableauLayout';

/**
 * windshieldTableauHelpers — WO-AAA-SOLAR-TABLEAU phase 3 module split.
 * Pure, DOM/hook-free geometry + travel-phase helpers extracted VERBATIM out
 * of WindshieldTableau.tsx (mechanical extraction only, to bring that file
 * back under the 1500-line TS cap — see the design brief's own "MODULE
 * CAVEAT", `audit/design-briefs/aaa-solar-tableau-2026-07-18.md`). Every
 * export here has ZERO closure over component state; each takes its inputs
 * as plain arguments, exactly as it did at module scope inside
 * WindshieldTableau.tsx before the move — behavior is unchanged, only the
 * file boundary moved. WindshieldTableau.tsx re-imports everything it still
 * needs from here and re-exports `distancePx` / `chooseWarpArrivalAnchor` /
 * `REFERENCE_BAND` / `ENGAGE_RANGE_EM` so existing external imports
 * (TacticalTargetPage.tsx's `from '../WindshieldTableau'`,
 * WindshieldTableau.test.tsx's `chooseWarpArrivalAnchor` import) keep
 * working unchanged.
 */

// ---------------------------------------------------------------------------
// Contract subset (mirrors SectorContentsResponse's static fields — see
// services/gameserver/src/api/routes/sectors.py's get_sector_contents).
// ---------------------------------------------------------------------------

export interface StaticSystem {
  star: { kind: string; label: string; color: string } | null;
  nebula: { hue: number; density: number } | null;
  belt: { inner_au: number; outer_au: number } | null;
  debris: { inner_au: number; outer_au: number; hue: number } | null;
  bodies: SystemBody[];
  stations: SystemStation[];
}

export const POPUP_W = 232;
export const POPUP_H = 158;

// Warp cinematic phase durations — imported from warpCinematicBus (single
// source of truth). Callers delay moveToSector by WARP_TURN_MS so the sector
// swap cannot abort the RCS reorientation. CSS keyframes in
// solar-system-viewscreen.css must stay in sync with these values.
export const DOCK_RANGE_EM = 5;
export const DOCK_APPROACH_STANDOFF_EM = 3.5;
/** WO-TACTICAL-APPROACH-ENGAGE-SCROLL Part B: how close (in REFERENCE_BAND
 *  em, the same canonical-%-space convention DOCK_RANGE_EM uses) a ship
 *  contact must be before TACTICAL TARGET's menu offers ENGAGE instead of
 *  APPROACH. PLACEHOLDER — a small multiple of DOCK_RANGE_EM as a sensible
 *  starting number; a Max-tunable dial once playtested, same convention as
 *  DOCK_LAND_PROXIMITY_RANGE_EM. Exported (like distancePx/REFERENCE_BAND
 *  below) so TacticalTargetPage.tsx computes the SAME range read the ship
 *  markers below are drawn from, not a second, independently-drifting copy. */
export const ENGAGE_RANGE_EM = DOCK_RANGE_EM * 3;
// Local intra-system flight is ONE continuous position glide (accelerate →
// cruise → decelerate, a single eased CSS transition over TRAVEL_MOVE_MS), with
// the engine burn, RCS jets, and the retrograde flip layered on as a timed
// track. The hull keeps coasting at speed through the flip — momentum, never a
// dead stop to turn. Phase boundaries only retoggle visuals / retime the NEXT
// rotation; they never restart the running position glide. TRAVEL_MOVE_MS must
// equal the 6.4s position duration in solar-system-viewscreen.css.
export const TRAVEL_ORIENT_MS = 1000;
export const TRAVEL_ACCEL_MS = 1800;
export const TRAVEL_COAST_MS = 1100;
export const TRAVEL_FLIP_MS = 1300;
export const TRAVEL_DECEL_MS = 2200;
export const TRAVEL_SETTLE_MS = 800;
export const TRAVEL_MOVE_MS = TRAVEL_ACCEL_MS + TRAVEL_COAST_MS + TRAVEL_FLIP_MS + TRAVEL_DECEL_MS;
/** Emergency Halt: flip then burn — shorter than a planned approach brake. */
export const TRAVEL_HALT_FLIP_MS = 1800;
export const TRAVEL_HALT_BRAKE_MS = 1600;
/** How far ahead (as a fraction of remaining path) the hull coasts while flipping. */
export const TRAVEL_HALT_COAST_FRAC = 0.38;
/** Mid-course redirect: RCS turn while the path arcs onto the new bearing. */
export const TRAVEL_REDIRECT_TURN_MS = 1600;

export type TravelPhase =
  | 'idle'
  | 'orienting'
  | 'accelerating'
  | 'gliding'
  | 'brake-turn'
  | 'braking'
  | 'final-orient'
  | 'halt-turn'
  | 'halt-brake'
  | 'redirect-turn';

/** Signed shortest angular delta from `from` to `to`, in (-180, 180]. */
export function shortestAngleDelta(from: number, to: number): number {
  return ((to - from + 540) % 360) - 180;
}

export function clampPct(n: number): number {
  return Math.min(98, Math.max(2, n));
}

/** Soft arc control point: keep coasting along current velocity, then bend toward the new target. */
export function redirectArcWaypoint(
  live: PctPoint,
  velocity: { x: number; y: number },
  target: PctPoint,
): PctPoint {
  const toTarget = Math.hypot(target.xPct - live.xPct, target.yPct - live.yPct);
  const lead = Math.min(Math.max(4, toTarget * 0.32), 14);
  const coast: PctPoint = {
    xPct: live.xPct + velocity.x * lead,
    yPct: live.yPct + velocity.y * lead,
  };
  return {
    xPct: clampPct(coast.xPct + (target.xPct - coast.xPct) * 0.4),
    yPct: clampPct(coast.yPct + (target.yPct - coast.yPct) * 0.4),
  };
}

export function isInFlightPhase(phase: TravelPhase): boolean {
  return (
    phase === 'accelerating' ||
    phase === 'gliding' ||
    phase === 'brake-turn' ||
    phase === 'braking' ||
    phase === 'halt-turn' ||
    phase === 'halt-brake' ||
    phase === 'redirect-turn'
  );
}

export function distancePx(a: PctPoint, b: PctPoint, band: BandGeometry): number {
  return Math.hypot(
    ((a.xPct - b.xPct) / 100) * band.widthPx,
    ((a.yPct - b.yPct) / 100) * band.heightPx,
  );
}

export interface ResolvedShipPose {
  xPct: number;
  yPct: number;
  headingDeg: number;
  burning: boolean;
  phaseClass: string;
}

/**
 * Resolve a ship contact's on-screen pose — server ISP pose/leg when the
 * server tracks one, else the local flight-profile fallback (cosmetic NPC
 * wander, or a stable parked anchor for a poseless human — see
 * FIX-POSELESS-FALLBACK below). Extracted (WO-TACTICAL-APPROACH-ENGAGE-
 * SCROLL Part B) from what was three independently-inlined copies of this
 * SAME branch: the `.other` render loop, the reticle-selection anchor
 * (`selectedPos`), and now the pendingApproach resolver's ship-glide-target
 * lookup + the per-tick publish into WindshieldFlightContext.contactPositions
 * — one source of truth for "where is this contact's dot right now", so a
 * proximity read (TACTICAL TARGET's Engage/Approach split) can never
 * disagree with where the dot is actually drawn.
 *
 * Pure — `nowMs` is passed in (rather than read via a closed-over
 * `ispNowMs()`) so this can live at module scope like every other geometry
 * helper in this file.
 */
export function resolveShipPose(
  s: ShipPresence,
  nowMs: number,
  contactT: number,
  contactDocks: ContactDock[],
  bandAspect: number,
): ResolvedShipPose {
  if (s.pose) {
    const sample = deriveIspPose(s.pose as IspPose, nowMs);
    return {
      xPct: sample.x_pct,
      yPct: sample.y_pct,
      headingDeg: sample.heading_deg,
      burning: !!sample.burning,
      phaseClass: ispPhaseToTravelClass(String(sample.phase)),
    };
  }
  if (s.is_npc) {
    // Decorative NPC traffic with no server-tracked pose --
    // otherShipFlightPose's cosmetic wander is what it was BUILT for (no
    // real position exists to render), unchanged.
    const pose = otherShipFlightPose(String(s.ship_id), contactT, contactDocks, {
      archetype: s.archetype,
      activity: s.activity,
      mission: s.mission,
      bandAspect,
    });
    return {
      xPct: pose.xPct,
      yPct: pose.yPct,
      headingDeg: pose.headingDeg,
      burning: pose.burning,
      phaseClass: pose.phase === 'brake-turn' ? 'brake-turn'
        : pose.phase === 'final-orient' ? 'final-orient'
        : pose.phase,
    };
  }
  // FIX-POSELESS-FALLBACK (P0): a HUMAN contact with no pose data is a REAL
  // player, not decorative traffic -- otherShipFlightPose is time-driven
  // (contactT), so reusing it here made a real player's dot "port" between
  // positions every poll instead of holding still. Render PARKED at a
  // stable, deterministic per-contact anchor instead (otherPresencePosition
  // -- the same per-UUID seed otherShipFlightPose itself starts from) until
  // real pose data arrives; identical inputs -> identical anchor on every
  // render, so both seats agree on where it's parked.
  const parked = otherPresencePosition(s.player_id || String(s.ship_id));
  return { xPct: parked.xPct, yPct: parked.yPct, headingDeg: 0, burning: false, phaseClass: 'idle' };
}

/** Stop near a station rather than directly on top of its glyph. */
export function stationApproachPoint(
  from: PctPoint,
  station: PctPoint,
  band: BandGeometry,
): PctPoint {
  const dxPx = ((from.xPct - station.xPct) / 100) * band.widthPx;
  const dyPx = ((from.yPct - station.yPct) / 100) * band.heightPx;
  const length = Math.hypot(dxPx, dyPx);
  const ux = length > 0.01 ? dxPx / length : 1;
  const uy = length > 0.01 ? dyPx / length : 0;
  const standOffPx = DOCK_APPROACH_STANDOFF_EM * band.remPx;
  return {
    xPct: Math.min(97, Math.max(3, station.xPct + (ux * standOffPx / band.widthPx) * 100)),
    yPct: Math.min(94, Math.max(6, station.yPct + (uy * standOffPx / band.heightPx) * 100)),
  };
}

/** FIX C revise (Max: right-click must be MENU-mediated, not direct-travel —
 *  corrects the earlier direct-travel cut): a small floating menu, sized for
 *  a single "Travel To" action button, not the 232px info card `.ssv-popup`
 *  is sized for. */
export const CTXMENU_W = 140;
export const CTXMENU_H = 40;

/** T1-A: a `.pl` planet disc's own rendered footprint — its `.pltag` label
 *  is position:absolute (escapes `.pl`'s own layout box, see solar-system-
 *  viewscreen.css), so the button's own bounding rect never exceeds
 *  BODY_SIZE_EM_MAX regardless of the planet's name. A small buffer above
 *  it covers box-shadow/outline paint that doesn't affect layout but is
 *  worth a little slack against sub-pixel rounding. */
export const PLANET_FOOTPRINT_EM_MAX = BODY_SIZE_EM_MAX + 0.2;

/** T1-A: a `.obj` station's own rendered footprint — UNLIKE `.pl`, `.obj`
 *  is `display:flex;flex-direction:column` with its `.objtag` NAME LABEL as
 *  a normal-flow child (not position:absolute), so the button's own
 *  bounding rect genuinely GROWS WIDER with the station's name length (nothing
 *  constrains `.obj`'s own width, so the label never wraps either) — a
 *  live-measured T1-A proof (zero-footprint Playwright harness, see this
 *  WO's own report) found ~28px base + ~7.4px/char at this module's own
 *  reference remPx (18.09px, 1440x900 flight-mode band). The 38-char name
 *  this codebase's own WindshieldTableau.test.tsx already exercises as its
 *  real long-name precedent ("Trade Hub Capelworks Expansion Complex" —
 *  that test's own WO-TABLEAU-TUNE #25 citation) predicts ~17.1em; 20em
 *  gives that a comfortable buffer. HEIGHT stays ~constant regardless of
 *  name length (the label never wraps) — the live proof measured ~4.25em;
 *  5em gives that buffer too.
 *
 *  This is a best-effort, empirically-grounded ceiling, NOT a mathematical
 *  guarantee for an arbitrarily long name — coordinate math alone can't
 *  bound an unbounded-width flex child; a true hard guarantee needs a CSS-
 *  side max-width/wrap constraint on `.objtag`, which is out of this WO's
 *  lane (solar-system-viewscreen.css is a concurrent lane) — flagged in
 *  this WO's own report rather than edited here. */
export const STATION_FOOTPRINT_EM_WIDTH_MAX = 20;
export const STATION_FOOTPRINT_EM_HEIGHT_MAX = 5;

/** Fallback px-per-1em if getComputedStyle can't resolve one yet (e.g. a
 *  jsdom test environment with no real CSS cascade) — the codebase's own
 *  nominal default em-root (windshieldTableauLayout.ts's MOON_DOT_*
 *  comment cites the same convention). */
export const DEFAULT_REM_PX = 16;

/** canonical-%-space (Max ruling): the client computes every ISP %-space
 *  POSITION from this FIXED reference band — identical to the server's own
 *  SectorLayout geometry by construction, at every real viewport/uiscale —
 *  never from `bandBox` (this component's own live `getBoundingClientRect`/
 *  ResizeObserver measurement, which the hub's fly-by proved diverges
 *  0.11-0.26% from the server across common viewports, since a real user's
 *  screen size/uiscale has nothing to do with the canonical layout the
 *  server independently computes). `bandBox` is NOT retired — it still
 *  drives every PURE-RENDERING concern (px-scaling of the %-results the
 *  browser already does via `left:X%`, label-clip lean decisions, visual
 *  heading/aspect-correction for the glyph rotation) where using the REAL
 *  viewport is not just safe but *correct* (a label's clip risk is a real-
 *  pixel fact, not a canonical one). See this WO's own report for the full
 *  per-consumer classification. Values match this module's own long-
 *  standing test fixture (`windshieldTableauLayout.test.ts`'s `FLIGHT_BAND`)
 *  — that fixture was already the ratified reference, just not yet wired
 *  into the runtime component itself. */
export const REFERENCE_BAND: BandGeometry = { widthPx: 1440, heightPx: 334.7, remPx: 18.09 };

export function toStaticSystem(data: any): StaticSystem {
  const d = data || {};
  return {
    star: d.star ?? null,
    nebula: d.nebula ?? null,
    belt: d.belt ?? null,
    debris: d.debris ?? null,
    bodies: Array.isArray(d.bodies) ? d.bodies : [],
    stations: Array.isArray(d.stations) ? d.stations : [],
  };
}

/**
 * Pick a fresh warp-in point while keeping the entire arrival bubble clear of
 * the destination star, planets, and station glyphs. Collision tests run in
 * real pixels (not raw x/y percentages — the windshield is very wide/short).
 * Random candidates make repeated arrivals vary; the deterministic grid is a
 * last-resort "farthest available" fallback for unusually crowded systems.
 */
export function chooseWarpArrivalAnchor(
  sectorId: number,
  snapshot: StaticSystem,
  band: BandGeometry,
  random: () => number = Math.random,
): PctPoint {
  const star = starAnchor(sectorId, snapshot.star, snapshot.bodies);
  const planetRadii = safeOrbitRadii(star, band, PLANET_FOOTPRINT_EM_MAX);
  const stationRadii = safeOrbitRadii(
    star,
    band,
    STATION_FOOTPRINT_EM_WIDTH_MAX,
    STATION_FOOTPRINT_EM_HEIGHT_MAX,
  );
  const bubbleRadiusPx = 1.7 * band.remPx;
  const clearancePx = 0.8 * band.remPx;
  const shipClearancePx = bubbleRadiusPx + clearancePx;

  type Obstacle =
    | { kind: 'circle'; xPx: number; yPx: number; radiusPx: number }
    | { kind: 'rect'; xPx: number; yPx: number; halfWidthPx: number; halfHeightPx: number };
  const toPx = (p: PctPoint) => ({
    xPx: (p.xPct / 100) * band.widthPx,
    yPx: (p.yPct / 100) * band.heightPx,
  });
  const obstacles: Obstacle[] = [];

  if (snapshot.star) {
    const p = toPx(star);
    obstacles.push({ kind: 'circle', ...p, radiusPx: (star.sizeEm * band.remPx) / 2 });
  }
  snapshot.bodies.forEach((body) => {
    const p = toPx(bodyPosition(star, body, planetRadii));
    obstacles.push({
      kind: 'circle',
      ...p,
      radiusPx: (bodySizeEm(body) * band.remPx) / 2,
    });
  });
  snapshot.stations.forEach((station) => {
    const p = toPx(stationPosition(star, station, stationRadii));
    obstacles.push({
      kind: 'rect',
      ...p,
      halfWidthPx: (STATION_FOOTPRINT_EM_WIDTH_MAX * band.remPx) / 2,
      halfHeightPx: (STATION_FOOTPRINT_EM_HEIGHT_MAX * band.remPx) / 2,
    });
  });

  const marginXPct = (shipClearancePx / band.widthPx) * 100;
  const marginYPct = (shipClearancePx / band.heightPx) * 100;
  const xMin = Math.max(6, marginXPct);
  const xMax = Math.min(94, 100 - marginXPct);
  const yMin = Math.max(10, marginYPct);
  const yMax = Math.min(90, 100 - marginYPct);

  const clearance = (candidate: PctPoint): number => {
    const p = toPx(candidate);
    if (obstacles.length === 0) return Number.POSITIVE_INFINITY;
    return Math.min(...obstacles.map((obstacle) => {
      const dx = Math.abs(p.xPx - obstacle.xPx);
      const dy = Math.abs(p.yPx - obstacle.yPx);
      if (obstacle.kind === 'circle') {
        return Math.hypot(dx, dy) - obstacle.radiusPx - shipClearancePx;
      }
      const outsideX = dx - obstacle.halfWidthPx - shipClearancePx;
      const outsideY = dy - obstacle.halfHeightPx - shipClearancePx;
      if (outsideX >= 0 || outsideY >= 0) {
        return Math.hypot(Math.max(0, outsideX), Math.max(0, outsideY));
      }
      return Math.max(outsideX, outsideY);
    }));
  };

  // A fresh random stream is consumed on every warp, so revisiting one sector
  // does not reuse its prior arrival coordinate.
  for (let attempt = 0; attempt < 160; attempt++) {
    const candidate = {
      xPct: xMin + Math.min(0.999999, Math.max(0, random())) * (xMax - xMin),
      yPct: yMin + Math.min(0.999999, Math.max(0, random())) * (yMax - yMin),
    };
    if (clearance(candidate) >= 0) return candidate;
  }

  // Extremely crowded fallback: choose the grid point with maximum clearance.
  let best: PctPoint = { xPct: (xMin + xMax) / 2, yPct: (yMin + yMax) / 2 };
  let bestClearance = clearance(best);
  for (let yi = 0; yi <= 10; yi++) {
    for (let xi = 0; xi <= 16; xi++) {
      const candidate = {
        xPct: xMin + (xi / 16) * (xMax - xMin),
        yPct: yMin + (yi / 10) * (yMax - yMin),
      };
      const candidateClearance = clearance(candidate);
      if (candidateClearance > bestClearance) {
        best = candidate;
        bestClearance = candidateClearance;
      }
    }
  }
  return best;
}

export interface PopupState {
  key: string;
  meta: HitMeta;
  name: string;
  xPct: number;
  yPct: number;
}

/** FIX C revise: the stashed right-click target -- a menu opens at
 *  (xPct,yPct) and NOTHING travels until the player explicitly picks
 *  "Travel To" (see handleContextMenu/handleTravelToClick below). */
export interface CtxMenuState {
  xPct: number;
  yPct: number;
}

export function arcPath(star: StarAnchor, arc: HazardArc): string {
  const rx = arc.rFrac * AU_SEMI_X_PCT;
  const ry = arc.rFrac * AU_SEMI_Y_PCT;
  const startRad = (arc.startDeg * Math.PI) / 180;
  const endRad = ((arc.startDeg + arc.sweepDeg) * Math.PI) / 180;
  const sx = (star.xPct + Math.cos(startRad) * rx).toFixed(2);
  const sy = (star.yPct + Math.sin(startRad) * ry).toFixed(2);
  const ex = (star.xPct + Math.cos(endRad) * rx).toFixed(2);
  const ey = (star.yPct + Math.sin(endRad) * ry).toFixed(2);
  const largeArc = arc.sweepDeg > 180 ? 1 : 0;
  return `M ${sx} ${sy} A ${rx.toFixed(2)} ${ry.toFixed(2)} 0 ${largeArc} 1 ${ex} ${ey}`;
}

/** T0-2 (orbit-line view): a single body's own thin orbit ellipse — reuses
 *  the SAME `.orbit` div idiom (and its exact CSS: 1px dashed, low-opacity,
 *  z-index:0 — solar-system-viewscreen.css) the retired generic
 *  decorativeRings already used, so the visual weight is unchanged; the
 *  geometry is now REAL (bodyOrbitEllipse derives the ellipse FROM the
 *  body's own already-computed position, T0-1's fan/rank placement
 *  untouched) instead of 4 fixed cosmetic radii. Rendered as a SIBLING
 *  immediately BEFORE its own body/station/wreck element (same map
 *  iteration, wrapped in a Fragment) so DOM order alone keeps it visually
 *  BEHIND that element even where the element has no explicit z-index of
 *  its own (`.obj` — CSS Appendix E: same-level (z:0/auto) siblings paint
 *  in tree order); `.pl`'s own explicit z-index:2 makes this doubly certain
 *  there. Returns null (renders nothing) for bodyOrbitEllipse's own
 *  degenerate case. */
export function orbitEllipse(star: StarAnchor, pos: PctPoint, key: string): React.ReactNode {
  const ellipse = bodyOrbitEllipse(star, pos);
  if (!ellipse) return null;
  return (
    <div
      key={key}
      className="orbit"
      style={{
        left: `${ellipse.cxPct}%`, top: `${ellipse.cyPct}%`,
        width: `${ellipse.rxPct * 2}%`, height: `${ellipse.ryPct * 2}%`,
        transform: 'translate(-50%,-50%)',
      }}
    />
  );
}
