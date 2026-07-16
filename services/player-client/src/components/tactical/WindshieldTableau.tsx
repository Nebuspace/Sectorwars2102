import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import apiClient from '../../services/apiClient';
import { arrivalBearingForWarp, WARP_TURN_MS, WARP_MIN_CHARGE_MS, WARP_ARRIVE_MS, WARP_CHARGE_TIMEOUT_MS } from '../../services/warpCinematicBus';
import { useAutopilot } from '../../contexts/AutopilotContext';
import { useWindshieldFlight } from '../../contexts/WindshieldFlightContext';
import type { SectorWreck } from '../../services/api';
import type { SpecialFormationSummary } from '../../contexts/GameContext';
import type {
  HitMeta,
  ShipPresence,
  SystemBody,
  SystemStation,
} from './SolarSystemViewscreen';
import { shipFaction } from './SolarSystemViewscreen';
import {
  deriveIspPose,
  ispPhaseToTravelClass,
  parseIspTime,
  type IspPose,
} from '../../services/intrasystemFlight';
import {
  AU_SEMI_X_PCT,
  AU_SEMI_Y_PCT,
  BODY_SIZE_EM_MAX,
  beltStyle,
  bodyOrbitEllipse,
  bodyPosition,
  bodySizeEm,
  debrisArc,
  headingDeg,
  moonOrbits,
  nebulaArcs,
  otherShipFlightPose,
  safeOrbitRadii,
  scanPosition,
  selfRestingAnchor,
  starAnchor,
  stationPosition,
  type BandGeometry,
  type ContactDock,
  type HazardArc,
  type PctPoint,
  type StarAnchor,
} from './windshieldTableauLayout';
import './solar-system-viewscreen.css';

/**
 * WindshieldTableau — the flight-mode windshield-band scene
 * (WO-UI2-WINDSHIELD-TABLEAU), replacing SolarSystemViewscreen's canvas
 * orrery with the ratified demo's STATIC DOM "sliver" composition (Max,
 * live-playtest #4: "a sliver of the solar system with all objects in it,
 * no rotating around the sun").
 *
 * SolarSystemViewscreen.tsx is intentionally left byte-for-byte untouched
 * for its 'flight' scene path (this WO stops MOUNTING it there, in
 * GameDashboard.tsx, rather than editing its canvas/orbital-closeup/popup
 * code) — see this component's own file-header verify-first note below for
 * why. It still owns 'docked' and 'landed' scenes unchanged, and CHART
 * 2D/3D (NavigationMap/Galaxy3DRenderer) is a wholly separate component,
 * also untouched.
 *
 * VERIFY-FIRST FINDING (orbital closeup): the WO's brief asked to leave
 * "orbital closeup" alone as a co-existing canvas painter, believing it was
 * a separate mount (like CHART). It is not — SolarSystemViewscreen.tsx's
 * `enterOrbit`/`drawOrbitCloseup` only ever triggers from a click inside the
 * SAME 'flight' canvas this WO replaces, via `handleClick`'s
 * `target.kind === 'planet'` branch (SolarSystemViewscreen.tsx, "Clicking a
 * planet zooms the windshield to an orbital closeup of it"). Since that
 * canvas is no longer mounted for flight, closeup becomes unreachable dead
 * code (harmless — the file is untouched, so nothing breaks; it simply has
 * no live entry point anymore). This tableau instead reuses the OTHER path
 * that file's own comment calls out as the deliberate LAND fallback:
 * "clicking a real planet now enters the orbital closeup... this popup
 * branch is a fallback only — kept for the LAND action if a planet popup is
 * ever opened by another path." That "another path" is this component's
 * click→popup→LAND flow (ssv-popup, reused verbatim) — the demo's own
 * idiom is exactly this simpler click-to-inspect model, not a full-screen
 * zoom.
 *
 * DATA: fetches GET /api/v1/sectors/{id}/contents (WO-UI2-INTRASYSTEM-MODEL,
 * ec21a3eb) once per sectorId change for the STATIC celestial composition
 * (star/bodies/stations/nebula/belt/debris/habitable_zone — the same fields
 * SolarSystemViewscreen.tsx's own GET /sectors/{id}/system already served,
 * unioned into the one consolidated read-only endpoint the backend shipped
 * specifically anticipating this FE pass). Live, WS-reactive data
 * (ships/wrecks/formations) stays on PROPS from GameDashboard exactly as
 * today, deliberately — /contents is a plain poll-once GET with no WS
 * push, and switching those three feeds to it would trade away the
 * liveness GameDashboard's currentSector context already provides for no
 * WO-required benefit.
 *
 * FLIGHT (WO-UI2-FLIGHT-FEEL): this component OWNS the actual click→glide
 * (`travelTo`, below) — it alone has the /contents system data needed to
 * resolve a planet/station id to a %-position and is the only thing that
 * renders/animates `.shipmk`. It publishes that state into the shared
 * WindshieldFlightContext (contexts/WindshieldFlightContext.tsx) so the
 * SOLAR SYSTEM monitor's per-row APPROACH/HALT and the locrow's ALL STOP
 * chip — previously wired to the unrelated inter-sector AutopilotContext —
 * read and drive the SAME real flight state a band-object click does.
 */

// ---------------------------------------------------------------------------
// Contract subset (mirrors SectorContentsResponse's static fields — see
// services/gameserver/src/api/routes/sectors.py's get_sector_contents).
// ---------------------------------------------------------------------------

interface StaticSystem {
  star: { kind: string; label: string; color: string } | null;
  nebula: { hue: number; density: number } | null;
  belt: { inner_au: number; outer_au: number } | null;
  debris: { inner_au: number; outer_au: number; hue: number } | null;
  bodies: SystemBody[];
  stations: SystemStation[];
}

export interface WindshieldTableauProps {
  sectorId: number;
  /** Cosmetic-only: tints the scene background when the sector is
   *  dangerous (demo's `sec.hazard>=5` → `.scene.space.hazard`). The
   *  Annunciator/locrow own the actual hazard READOUT — this is background
   *  chrome only. */
  hazardLevel?: number;
  /** Real DB planet records (owner_name etc.) — /contents' bodies carry
   *  `owned` but not `owner_name`; this stays a prop exactly as
   *  SolarSystemViewscreen.tsx already receives it. */
  planets?: Array<{ id: string; owner_name?: string | null; owner_id?: string | null }>;
  ships?: ShipPresence[];
  wrecks?: SectorWreck[];
  formations?: SpecialFormationSummary[];
  scanActive?: boolean;
  onRequestLand?: (planetId: string) => void;
  onRequestDock?: (stationId: string) => void;
  selectedShipId?: string | null;
  onSelectShip?: (id: string) => void;
  /** Max refinement (5b): "undock emerges at the host's position" — the
   *  station/planet id the player just left, so the ship's FIRST frame in
   *  this fresh mount starts there instead of a generic seeded anchor.
   *  GameDashboard tracks these via a ref that survives the docked/landed
   *  unmount boundary (this component itself remounts on every
   *  dock↔flight/land↔flight transition, per the existing conditional
   *  mount structure — see GameDashboard.tsx). */
  lastDockedStationId?: string | null;
  lastLandedPlanetId?: string | null;
  /** Warp cinematic trigger. GameDashboard bumps `token` (and supplies the
   *  exit `bearingDeg`) the instant the player commits to an inter-sector
   *  jump — BEFORE the move resolves — so the buildup ("charging") + warp-away
   *  ("launch") play over the CURRENT sector, then the sector swaps and the
   *  arrival flash lands. Null / unchanged token = no cinematic (e.g. autopilot
   *  hops, which jump silently). */
  warpDepart?: { token: number; bearingDeg: number; destinationSectorId: number } | null;
}

const POPUP_W = 232;
const POPUP_H = 158;

// Warp cinematic phase durations — imported from warpCinematicBus (single
// source of truth). Callers delay moveToSector by WARP_TURN_MS so the sector
// swap cannot abort the RCS reorientation. CSS keyframes in
// solar-system-viewscreen.css must stay in sync with these values.
const DOCK_RANGE_EM = 5;
const DOCK_APPROACH_STANDOFF_EM = 3.5;
// Local intra-system flight is ONE continuous position glide (accelerate →
// cruise → decelerate, a single eased CSS transition over TRAVEL_MOVE_MS), with
// the engine burn, RCS jets, and the retrograde flip layered on as a timed
// track. The hull keeps coasting at speed through the flip — momentum, never a
// dead stop to turn. Phase boundaries only retoggle visuals / retime the NEXT
// rotation; they never restart the running position glide. TRAVEL_MOVE_MS must
// equal the 6.4s position duration in solar-system-viewscreen.css.
const TRAVEL_ORIENT_MS = 1000;
const TRAVEL_ACCEL_MS = 1800;
const TRAVEL_COAST_MS = 1100;
const TRAVEL_FLIP_MS = 1300;
const TRAVEL_DECEL_MS = 2200;
const TRAVEL_SETTLE_MS = 800;
const TRAVEL_MOVE_MS = TRAVEL_ACCEL_MS + TRAVEL_COAST_MS + TRAVEL_FLIP_MS + TRAVEL_DECEL_MS;
/** Emergency Halt: flip then burn — shorter than a planned approach brake. */
const TRAVEL_HALT_FLIP_MS = 1800;
const TRAVEL_HALT_BRAKE_MS = 1600;
/** How far ahead (as a fraction of remaining path) the hull coasts while flipping. */
const TRAVEL_HALT_COAST_FRAC = 0.38;
/** Mid-course redirect: RCS turn while the path arcs onto the new bearing. */
const TRAVEL_REDIRECT_TURN_MS = 1600;

type TravelPhase =
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
function shortestAngleDelta(from: number, to: number): number {
  return ((to - from + 540) % 360) - 180;
}

function clampPct(n: number): number {
  return Math.min(98, Math.max(2, n));
}

/** Soft arc control point: keep coasting along current velocity, then bend toward the new target. */
function redirectArcWaypoint(
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

function isInFlightPhase(phase: TravelPhase): boolean {
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

function distancePx(a: PctPoint, b: PctPoint, band: BandGeometry): number {
  return Math.hypot(
    ((a.xPct - b.xPct) / 100) * band.widthPx,
    ((a.yPct - b.yPct) / 100) * band.heightPx,
  );
}

/** Stop near a station rather than directly on top of its glyph. */
function stationApproachPoint(
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
const CTXMENU_W = 140;
const CTXMENU_H = 40;

/** T1-A: a `.pl` planet disc's own rendered footprint — its `.pltag` label
 *  is position:absolute (escapes `.pl`'s own layout box, see solar-system-
 *  viewscreen.css), so the button's own bounding rect never exceeds
 *  BODY_SIZE_EM_MAX regardless of the planet's name. A small buffer above
 *  it covers box-shadow/outline paint that doesn't affect layout but is
 *  worth a little slack against sub-pixel rounding. */
const PLANET_FOOTPRINT_EM_MAX = BODY_SIZE_EM_MAX + 0.2;

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
const STATION_FOOTPRINT_EM_WIDTH_MAX = 20;
const STATION_FOOTPRINT_EM_HEIGHT_MAX = 5;

/** Fallback px-per-1em if getComputedStyle can't resolve one yet (e.g. a
 *  jsdom test environment with no real CSS cascade) — the codebase's own
 *  nominal default em-root (windshieldTableauLayout.ts's MOON_DOT_*
 *  comment cites the same convention). */
const DEFAULT_REM_PX = 16;

function toStaticSystem(data: any): StaticSystem {
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

interface PopupState {
  key: string;
  meta: HitMeta;
  name: string;
  xPct: number;
  yPct: number;
}

/** FIX C revise: the stashed right-click target -- a menu opens at
 *  (xPct,yPct) and NOTHING travels until the player explicitly picks
 *  "Travel To" (see handleContextMenu/handleTravelToClick below). */
interface CtxMenuState {
  xPct: number;
  yPct: number;
}

function arcPath(star: StarAnchor, arc: HazardArc): string {
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
function orbitEllipse(star: StarAnchor, pos: PctPoint, key: string): React.ReactNode {
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

const WindshieldTableau: React.FC<WindshieldTableauProps> = ({
  sectorId,
  hazardLevel = 0,
  planets = [],
  ships = [],
  wrecks = [],
  formations = [],
  scanActive = false,
  onRequestLand,
  onRequestDock,
  selectedShipId = null,
  onSelectShip,
  lastDockedStationId = null,
  lastLandedPlanetId = null,
  warpDepart = null,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  // T1-A: real measured band geometry (`.ssv-tableau`'s own rect, 100% of
  // `.band`) — the ONE thing safeOrbitRadii needs that this component alone
  // can supply (the layout module stays DOM-free). Measured synchronously
  // via useLayoutEffect (so it's set before the FIRST paint, well before
  // `system`'s async fetch resolves and bodies actually render) and kept
  // live via ResizeObserver — flight mode's own band height can change
  // mid-mount (18.5em rest <-> 12.5em ARIA-2 panel, cockpit-shell.css) even
  // though WindshieldTableau itself doesn't remount for that.
  const [bandBox, setBandBox] = useState<BandGeometry | null>(null);
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const remPx = parseFloat(getComputedStyle(el).fontSize) || DEFAULT_REM_PX;
      setBandBox({ widthPx: rect.width, heightPx: rect.height, remPx });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const [system, setSystem] = useState<StaticSystem | null>(null);
  const [fetchFailed, setFetchFailed] = useState(false);
  const [popup, setPopup] = useState<PopupState | null>(null);
  const [ctxMenu, setCtxMenu] = useState<CtxMenuState | null>(null);
  const ctxMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setSystem(null);
    setFetchFailed(false);
    setPopup(null);
    apiClient
      .get(`/api/v1/sectors/${sectorId}/contents`)
      .then((res) => {
        if (cancelled) return;
        setSystem(toStaticSystem(res.data));
      })
      .catch((err) => {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.error('WindshieldTableau: sector contents fetch failed:', err);
        setFetchFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sectorId]);

  const star = useMemo(
    () => starAnchor(sectorId, system?.star ?? null, system?.bodies ?? []),
    [sectorId, system?.star, system?.bodies]
  );
  // T1-A: undefined until the container's real geometry is measured (first
  // paint only) — orbitalPosition's own `!safeRadii` branch covers that
  // brief gap with the pre-T1-A unclamped math, harmlessly, since no body
  // renders until `system` resolves anyway (well after this is set). Two
  // SEPARATE radii sets — planets don't need the much-wider station margin
  // (see STATION_FOOTPRINT_EM_WIDTH_MAX's own doc-comment), so sharing one
  // would needlessly crush the planet sliver's spread.
  const safeRadiiPlanets = useMemo(
    () => (bandBox ? safeOrbitRadii(star, bandBox, PLANET_FOOTPRINT_EM_MAX) : undefined),
    [star, bandBox]
  );
  const safeRadiiStations = useMemo(
    () => (bandBox ? safeOrbitRadii(star, bandBox, STATION_FOOTPRINT_EM_WIDTH_MAX, STATION_FOOTPRINT_EM_HEIGHT_MAX) : undefined),
    [star, bandBox]
  );
  const belt = useMemo(() => (system?.belt ? beltStyle(star) : null), [star, system?.belt]);
  const hazeArcs = useMemo(() => (system?.nebula ? nebulaArcs(sectorId) : []), [sectorId, system?.nebula]);
  const debrisRingArc = useMemo(() => (system?.debris ? debrisArc(system.debris) : null), [system?.debris]);

  // Real planet/station anchors contacts fly between (same procedure as .shipmk).
  // Tag habitable vs barren so cosmetic fallback matches server destination bias.
  const contactDocks = useMemo((): ContactDock[] => {
    if (!system) return [];
    const docks: ContactDock[] = [];
    const habKinds = new Set(['TERRAN', 'OCEANIC', 'TROPICAL', 'JUNGLE']);
    for (const body of system.bodies) {
      const pos = bodyPosition(star, body, safeRadiiPlanets);
      const kind = String(body.kind || '').toUpperCase().replace(/^PLANETTYPE\./, '');
      const hab = typeof body.habitability === 'number' ? body.habitability : null;
      const habitable = habKinds.has(kind) || (hab != null && hab >= 50);
      docks.push({ ...pos, bucket: habitable ? 'habitable' : 'barren' });
    }
    for (const st of system.stations) {
      docks.push({ ...stationPosition(star, st, safeRadiiStations), bucket: 'habitable' });
    }
    return docks;
  }, [system, star, safeRadiiPlanets, safeRadiiStations]);

  // ---- Player's own ship marker — the ONLY system-level mover. ----
  const [shipPos, setShipPos] = useState<PctPoint | null>(null);
  const [heading, setHeading] = useState(0);
  const headingRef = useRef(heading);
  headingRef.current = heading;
  const [localBurn, setLocalBurn] = useState(false);
  const [travelPhase, setTravelPhase] = useState<TravelPhase>('idle');
  const travelPhaseRef = useRef<TravelPhase>('idle');
  travelPhaseRef.current = travelPhase;
  const travelTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const clearTravelTimers = useCallback(() => {
    travelTimersRef.current.forEach(clearTimeout);
    travelTimersRef.current = [];
  }, []);
  // The current glide's target planet/station id, or null (star/ship/wreck/
  // formation clicks, or no glide in progress) — published to the shared
  // flight context below so a SOLAR row can tell whether IT is the thing
  // being approached (WO-UI2-FLIGHT-FEEL).
  const [glideTargetId, setGlideTargetId] = useState<string | null>(null);
  const shipPosRef = useRef<PctPoint | null>(null);
  shipPosRef.current = shipPos;
  const travelOriginRef = useRef<PctPoint | null>(null);
  const shipMkRef = useRef<HTMLDivElement>(null);
  const seededSectorRef = useRef<number | null>(null);
  const autopilot = useAutopilot();
  const flight = useWindshieldFlight();

  // ---- Warp cinematic (sphere-field jump between sectors) ----
  //   idle → charging (bubble inflates around the parked hull; HOLDS over the
  //                    current sector until the jump actually resolves)
  //        → launch   (field snaps, ship streaks out along the exit bearing)
  //        → arriving (a warp flash as the destination sector takes over)
  //        → idle
  // The buildup is kicked by `warpDepart.token` (fired the instant the jump is
  // committed); the warp-away is keyed to the sectorId prop actually changing,
  // so the buildup always precedes the swap without delaying the move itself.
  // reduced-motion callers never send a token, so the whole thing no-ops.
  const [warpPhase, setWarpPhase] = useState<'idle' | 'turning' | 'charging' | 'launch' | 'arriving'>('idle');
  const warpPhaseRef = useRef(warpPhase);
  warpPhaseRef.current = warpPhase;
  const warpBearing = warpDepart?.bearingDeg ?? 0;
  const arrivalBearing = warpDepart
    ? arrivalBearingForWarp(warpBearing, warpDepart.token)
    : 0;
  const warpTokenSeenRef = useRef<number | null>(null);
  const chargeStartRef = useRef(0);
  const [preparedArrival, setPreparedArrival] = useState<{
    token: number;
    sectorId: number;
    point: PctPoint;
  } | null>(null);
  const warpTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const clearWarpTimers = useCallback(() => {
    warpTimersRef.current.forEach(clearTimeout);
    warpTimersRef.current = [];
  }, []);

  // Prefetch destination geometry while the departure bubble is charging.
  // That lets us choose an object-safe random point BEFORE the sector swaps,
  // so the destination's first arrival frame already has ship+bubble centered
  // together instead of correcting position afterward.
  useEffect(() => {
    if (!warpDepart || !bandBox) return;
    let cancelled = false;
    const { token, destinationSectorId } = warpDepart;
    setPreparedArrival((current) => (current?.token === token ? current : null));
    apiClient
      .get(`/api/v1/sectors/${destinationSectorId}/contents`)
      .then((res) => {
        if (cancelled) return;
        const snapshot = toStaticSystem(res.data);
        setPreparedArrival({
          token,
          sectorId: destinationSectorId,
          point: chooseWarpArrivalAnchor(destinationSectorId, snapshot, bandBox),
        });
      })
      .catch((err) => {
        if (cancelled) return;
        // Do not invent an unsafe fallback point: the normal destination fetch
        // can retry after the sector changes, while the launch keeps the old
        // hull hidden.
        // eslint-disable-next-line no-console
        console.error('WindshieldTableau: warp-arrival prefetch failed:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [warpDepart, bandBox]);

  // If the prefetch lost a race or transiently failed, the normal destination
  // fetch supplies the same geometry once `sectorId` changes. Still do not
  // reveal the arrival until an avoidance-checked point exists.
  useEffect(() => {
    if (!warpDepart || !bandBox || !system) return;
    if (warpDepart.destinationSectorId !== sectorId) return;
    if (preparedArrival?.token === warpDepart.token) return;
    setPreparedArrival({
      token: warpDepart.token,
      sectorId,
      point: chooseWarpArrivalAnchor(sectorId, system, bandBox),
    });
  }, [warpDepart, bandBox, system, sectorId, preparedArrival?.token]);

  // Buildup: a fresh token starts the bubble inflating over the CURRENT sector,
  // then the launch streak plays there. Arrival (sectorId effect below) cuts in
  // when the destination actually lands — snapping the hull to the new anchor.
  useEffect(() => {
    if (!warpDepart) return;
    if (warpTokenSeenRef.current === warpDepart.token) return;
    warpTokenSeenRef.current = warpDepart.token;
    clearWarpTimers();
    clearTravelTimers();
    setTravelPhase('idle');
    setLocalBurn(false);
    setGlideTargetId(null);
    // Phase 1 — TURN: re-orient the hull toward the exit bearing (RCS jets
    // puffing) BEFORE anything else. The warp field does not start inflating
    // until this turn has visibly finished. `bearingDeg` is the real galactic
    // XYZ vector projected onto this 2D view when coordinates are available
    // (deterministic fallback otherwise).
    setHeading(warpDepart.bearingDeg);
    setWarpPhase('turning');
    const timers = warpTimersRef.current;
    // Phase 2 — CHARGE: only after the turn completes does the bubble inflate.
    timers.push(
      setTimeout(() => {
        if (warpPhaseRef.current === 'turning') {
          chargeStartRef.current = Date.now();
          setWarpPhase('charging');
        }
      }, WARP_TURN_MS)
    );
    // Phase 3 — LAUNCH streak, once the field is fully charged.
    timers.push(
      setTimeout(() => {
        if (warpPhaseRef.current === 'charging') setWarpPhase('launch');
      }, WARP_TURN_MS + WARP_MIN_CHARGE_MS)
    );
    timers.push(
      setTimeout(() => {
        if (
          warpPhaseRef.current === 'turning' ||
          warpPhaseRef.current === 'charging' ||
          warpPhaseRef.current === 'launch'
        ) {
          setWarpPhase('idle');
        }
      }, WARP_CHARGE_TIMEOUT_MS)
    );
  }, [warpDepart, clearWarpTimers, clearTravelTimers]);

  // Arrival: sector swapped. Never cut the TURN short — if the move resolved
  // early (or a stale race), hold until the hull has finished re-orienting.
  // Snap the hull onto the NEW resting anchor IMMEDIATELY (`.shipmk` otherwise
  // CSS-glides left/top over 11.6s — bubble has no transition, so it jumps
  // while the ship crawls from the old sector's coordinates). Then play the
  // collapsing arrival sphere with the ship already centered inside it.
  useEffect(() => {
    if (warpPhase === 'turning' || warpPhase === 'idle') return;
    if (warpPhase !== 'charging' && warpPhase !== 'launch') return;
    if (!warpDepart || warpDepart.destinationSectorId !== sectorId) return;
    if (
      !preparedArrival ||
      preparedArrival.token !== warpDepart.token ||
      preparedArrival.sectorId !== sectorId
    ) return;
    clearWarpTimers();
    const arriveAt = preparedArrival.point;
    seededSectorRef.current = sectorId;
    shipPosRef.current = arriveAt;
    setShipPos(arriveAt);
    // Arrive on a deliberately different angle from departure. The inbound
    // streak uses this same bearing so hull orientation and motion agree.
    setHeading(arrivalBearing);
    setLocalBurn(false);
    setGlideTargetId(null);
    setWarpPhase('arriving');
    warpTimersRef.current = [
      setTimeout(() => setWarpPhase('idle'), WARP_ARRIVE_MS),
    ];
    return () => clearWarpTimers();
  }, [sectorId, warpDepart, preparedArrival, arrivalBearing, warpPhase, clearWarpTimers]);

  useEffect(() => () => clearWarpTimers(), [clearWarpTimers]);

  useEffect(() => {
    if (!system) return; // wait for the fetch that resolves dock/land host lookups
    if (seededSectorRef.current === sectorId) return;
    // A warp arrival owns this sector's first ship position. Wait for its
    // prefetched object-safe random anchor instead of applying the old
    // deterministic `selfRestingAnchor(sectorId)` first.
    if (
      warpDepart?.destinationSectorId === sectorId &&
      (warpPhaseRef.current === 'turning' ||
        warpPhaseRef.current === 'charging' ||
        warpPhaseRef.current === 'launch')
    ) return;
    seededSectorRef.current = sectorId;
    let anchor: PctPoint | null = null;
    if (lastDockedStationId) {
      const st = system.stations.find((s) => s.station_id === lastDockedStationId);
      if (st) anchor = stationPosition(star, st, safeRadiiStations);
    }
    if (!anchor && lastLandedPlanetId) {
      const b = system.bodies.find((bb) => bb.planet_id === lastLandedPlanetId);
      if (b) anchor = bodyPosition(star, b, safeRadiiPlanets);
    }
    if (!anchor) anchor = selfRestingAnchor(sectorId);
    // Sector reseed is a teleport, not a glide — suppress the 11.6s left/top
    // transition (warp-arriving / warp-launching CSS also forces this).
    shipPosRef.current = anchor;
    setShipPos(anchor);
    setHeading(0);
    // Emerging at a resting anchor (undock, planet lift-off, or a fresh
    // sector arrival) is NOT travel — the ship is parked, so the exhaust
    // flame must be cold. Clear any burn carried in from a prior glide.
    setLocalBurn(false);
    setGlideTargetId(null);
  }, [system, sectorId, lastDockedStationId, lastLandedPlanetId, star, safeRadiiPlanets, safeRadiiStations, warpDepart]);

  // FIX B: real band aspect (heightPx/widthPx) so headingDeg converts %-space
  // deltas into the same px-equivalent units before computing the angle --
  // see that function's own doc-comment. Falls back to 1 (square, the old
  // behavior) before bandBox is measured, matching headingDeg's own default.
  const bandAspect = useMemo(() => (bandBox ? bandBox.heightPx / bandBox.widthPx : 1), [bandBox]);

  // Contact traffic clock — drives ISP plan interpolation (~20fps).
  const [contactT, setContactT] = useState(0);
  const [ispClockSkewMs, setIspClockSkewMs] = useState(0); // server_time - local
  const [selfIspPose, setSelfIspPose] = useState<IspPose | null>(null);
  useEffect(() => {
    if (ships.length === 0 && !selfIspPose?.leg) return;
    const reduce =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    if (reduce) {
      setContactT(0);
      return;
    }
    let raf = 0;
    let lastPaint = 0;
    const tick = (now: number) => {
      if (now - lastPaint >= 50) {
        lastPaint = now;
        setContactT(now / 1000);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ships.length, selfIspPose?.leg]);

  // Hydrate authoritative self pose (reload / sector entry).
  // Must claim `seededSectorRef` when applied — otherwise the resting-anchor
  // seed (below) races in after /system resolves and snaps the ship back.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .get('/api/v1/helm/intrasystem/pose')
      .then((res) => {
        if (cancelled) return;
        const data = res.data as IspPose;
        if (data?.server_time) {
          setIspClockSkewMs(parseIspTime(data.server_time) - Date.now());
        }
        setSelfIspPose(data);
        const sample = deriveIspPose(data, Date.now() + (data?.server_time ? parseIspTime(data.server_time) - Date.now() : 0));
        // Undock/land emerge owns the first frame — don't stomp with a prior ISP.
        if (lastDockedStationId || lastLandedPlanetId) return;
        // Only teleport onto server pose when not mid local CSS glide.
        if (travelPhaseRef.current === 'idle') {
          seededSectorRef.current = sectorId;
          shipPosRef.current = { xPct: sample.x_pct, yPct: sample.y_pct };
          setShipPos({ xPct: sample.x_pct, yPct: sample.y_pct });
          setHeading(sample.heading_deg);
          setLocalBurn(!!sample.burning);
        }
      })
      .catch(() => { /* pose endpoint may lag deploy — keep local flight */ });
    return () => { cancelled = true; };
  }, [sectorId, lastDockedStationId, lastLandedPlanetId]);

  const ispNowMs = () => Date.now() + ispClockSkewMs;

  const commitIspBurn = useCallback((target: PctPoint, objectId: string | null) => {
    apiClient
      .post('/api/v1/helm/intrasystem/burn', {
        x_pct: target.xPct,
        y_pct: target.yPct,
        // Kind is informational; coords are authoritative. Free-point = point.
        target_kind: objectId ? 'object' : 'point',
        target_id: objectId,
      })
      .then((res) => {
        const data = res.data as IspPose;
        if (data?.server_time) setIspClockSkewMs(parseIspTime(data.server_time) - Date.now());
        setSelfIspPose(data);
      })
      .catch(() => { /* optimistic local flight still runs */ });
  }, []);

  const commitIspHalt = useCallback(() => {
    apiClient
      .post('/api/v1/helm/intrasystem/halt')
      .then((res) => {
        const data = res.data as IspPose;
        if (data?.server_time) setIspClockSkewMs(parseIspTime(data.server_time) - Date.now());
        setSelfIspPose(data);
      })
      .catch(() => {});
  }, []);

  const readLiveShipPos = useCallback((): PctPoint | null => {
    const containerEl = containerRef.current;
    const shipEl = shipMkRef.current;
    if (!containerEl || !shipEl) return shipPosRef.current;
    const containerRect = containerEl.getBoundingClientRect();
    const shipRect = shipEl.getBoundingClientRect();
    if (containerRect.width <= 0 || containerRect.height <= 0) return shipPosRef.current;
    return {
      xPct: ((shipRect.left + shipRect.width / 2 - containerRect.left) / containerRect.width) * 100,
      yPct: ((shipRect.top + shipRect.height / 2 - containerRect.top) / containerRect.height) * 100,
    };
  }, []);

  /** Schedule coast → flip → brake → face after the burn/glide has already been committed. */
  const armArrivalProfile = useCallback((prograde: number) => {
    const retrograde = prograde + 180;
    const faceDestination = prograde + 360;
    const timers = travelTimersRef.current;
    timers.push(setTimeout(() => {
      setTravelPhase('gliding');
      setLocalBurn(false);
    }, TRAVEL_ACCEL_MS));
    timers.push(setTimeout(() => {
      setTravelPhase('brake-turn');
      setHeading(retrograde);
    }, TRAVEL_ACCEL_MS + TRAVEL_COAST_MS));
    timers.push(setTimeout(() => {
      setTravelPhase('braking');
      setLocalBurn(true);
    }, TRAVEL_ACCEL_MS + TRAVEL_COAST_MS + TRAVEL_FLIP_MS));
    timers.push(setTimeout(() => {
      setTravelPhase('final-orient');
      setLocalBurn(false);
      setHeading(faceDestination);
    }, TRAVEL_MOVE_MS));
    timers.push(setTimeout(() => {
      setTravelPhase('idle');
      setGlideTargetId(null);
    }, TRAVEL_MOVE_MS + TRAVEL_SETTLE_MS));
  }, []);

  const travelTo = useCallback((target: PctPoint, objectId: string | null = null) => {
    clearTravelTimers();
    const phase = travelPhaseRef.current;

    // ── Mid-course redirect: keep momentum and arc onto the new bearing. ──
    // Never drop back into parked `orienting` — that freezes left/top and
    // reads as an instant momentum kill.
    if (isInFlightPhase(phase)) {
      const oldDest = shipPosRef.current ?? target;
      const origin = travelOriginRef.current;
      let live = readLiveShipPos() ?? oldDest;
      // jsdom / unsampled transitions report the style end-target; synthesize
      // a mid-course point from the recorded origin so the arc still has a
      // forward velocity vector to preserve.
      if (
        origin &&
        Math.hypot(oldDest.xPct - live.xPct, oldDest.yPct - live.yPct) < 0.4
      ) {
        live = {
          xPct: origin.xPct + (oldDest.xPct - origin.xPct) * 0.45,
          yPct: origin.yPct + (oldDest.yPct - origin.yPct) * 0.45,
        };
      }
      let vx = oldDest.xPct - (origin?.xPct ?? live.xPct);
      let vy = oldDest.yPct - (origin?.yPct ?? live.yPct);
      let vLen = Math.hypot(vx, vy);
      if (vLen < 1e-3) {
        vx = target.xPct - live.xPct;
        vy = target.yPct - live.yPct;
        vLen = Math.hypot(vx, vy);
      }
      if (vLen < 1e-3 || Math.hypot(target.xPct - live.xPct, target.yPct - live.yPct) < 0.1) {
        setLocalBurn(false);
        setTravelPhase('idle');
        setGlideTargetId(null);
        return;
      }
      vx /= vLen;
      vy /= vLen;

      const waypoint = redirectArcWaypoint(live, { x: vx, y: vy }, target);
      const arcHeading =
        headingRef.current + shortestAngleDelta(
          headingRef.current,
          headingDeg(live, waypoint, bandAspect),
        );
      const prograde =
        headingRef.current + shortestAngleDelta(
          headingRef.current,
          headingDeg(waypoint, target, bandAspect),
        );

      travelOriginRef.current = live;
      setGlideTargetId(objectId);
      setLocalBurn(false);
      setTravelPhase('redirect-turn');
      setHeading(arcHeading);
      // Retarget the running glide onto the arc waypoint — browser continues
      // from the live interpolated position (momentum preserved).
      setShipPos(waypoint);
      shipPosRef.current = waypoint;

      const timers = travelTimersRef.current;
      timers.push(setTimeout(() => {
        setTravelPhase('accelerating');
        setLocalBurn(true);
        setHeading(prograde);
        setShipPos(target);
        shipPosRef.current = target;
        travelOriginRef.current = waypoint;
        armArrivalProfile(prograde);
        commitIspBurn(target, objectId);
      }, TRAVEL_REDIRECT_TURN_MS));
      return;
    }

    // ── Cold start from a parked hull. ──
    const from = shipPosRef.current ?? target;
    const moving = Math.hypot(target.xPct - from.xPct, target.yPct - from.yPct) > 0.1;
    if (!moving) {
      setLocalBurn(false);
      setTravelPhase('idle');
      setGlideTargetId(null);
      return;
    }

    travelOriginRef.current = from;
    const prograde =
      headingRef.current + shortestAngleDelta(headingRef.current, headingDeg(from, target, bandAspect));

    setGlideTargetId(objectId);
    setLocalBurn(false);
    setTravelPhase('orienting');
    setHeading(prograde);
    commitIspBurn(target, objectId);

    const timers = travelTimersRef.current;
    timers.push(setTimeout(() => {
      setTravelPhase('accelerating');
      setLocalBurn(true);
      setShipPos(target);
      shipPosRef.current = target;
      armArrivalProfile(prograde);
    }, TRAVEL_ORIENT_MS));
  }, [bandAspect, clearTravelTimers, readLiveShipPos, armArrivalProfile, commitIspBurn]);

  const approachStation = useCallback((station: SystemStation, stationPos: PctPoint) => {
    if (!bandBox) return;
    const from = (isInFlightPhase(travelPhaseRef.current) ? readLiveShipPos() : null)
      ?? shipPosRef.current
      ?? stationPos;
    travelTo(stationApproachPoint(from, stationPos, bandBox), station.station_id);
  }, [bandBox, travelTo, readLiveShipPos]);

  const localTraveling = travelPhase !== 'idle';
  const burning = localBurn || autopilot.status === 'engaged';

  // Publish this component's real flight state into the shared context on
  // every change, so PlanetPortPair rows + the locrow ALL STOP chip
  // (GameDashboard.tsx) see the SAME glide a band-object click drives.
  useEffect(() => {
    flight.reportFlightState(localTraveling || autopilot.status === 'engaged', glideTargetId);
  }, [localTraveling, autopilot.status, glideTargetId, flight.reportFlightState]);

  // A SOLAR row's "APPROACH ▸" click records a request on the shared
  // context (GameDashboard.tsx -> PlanetPortPair's onApproach ->
  // flight.approach(id)); resolve it against the fetched system data and
  // run the SAME glide a direct band click performs — reuse, don't fork.
  useEffect(() => {
    if (!flight.pendingApproach || !system) return;
    const { objectId } = flight.pendingApproach;
    const bodyMatch = system.bodies.find((b) => b.real && b.planet_id === objectId);
    if (bodyMatch) {
      travelTo(bodyPosition(star, bodyMatch, safeRadiiPlanets), objectId);
      return;
    }
    const stationMatch = system.stations.find((s) => s.station_id === objectId);
    if (stationMatch) {
      const stationPos = stationPosition(star, stationMatch, safeRadiiStations);
      approachStation(stationMatch, stationPos);
    }
    // Unresolvable (stale id from a since-changed sector) — no-op, matches
    // the context's own documented "no-op if the id can't be resolved".
  }, [flight.pendingApproach, system, star, safeRadiiPlanets, safeRadiiStations, travelTo, approachStation]);

  // A row/locrow ALL STOP click (flight.allStop()) bumps stopSignal. Instead of
  // freezing momentum in place, abort the planned destination and run an
  // emergency flip → retro-burn: the hull keeps coasting a short distance
  // while it reorients, then burns to a stop. Orienting (not yet moving) just
  // cancels. Already-halting / already-braking for the destination is a no-op.
  useEffect(() => {
    if (flight.stopSignal === 0) return; // 0 = never stopped yet — skip the mount-time run
    const phase = travelPhaseRef.current;
    if (
      phase === 'idle' ||
      phase === 'halt-turn' ||
      phase === 'halt-brake' ||
      phase === 'brake-turn' ||
      phase === 'braking' ||
      phase === 'final-orient'
    ) {
      return;
    }

    clearTravelTimers();
    commitIspHalt();

    // Still parked while aiming — no momentum to bleed; just cancel.
    if (phase === 'orienting') {
      setLocalBurn(false);
      setTravelPhase('idle');
      setGlideTargetId(null);
      return;
    }

    const containerEl = containerRef.current;
    const shipEl = shipMkRef.current;
    const dest = shipPosRef.current;
    if (!containerEl || !shipEl || !dest) {
      setLocalBurn(false);
      setTravelPhase('idle');
      setGlideTargetId(null);
      return;
    }

    const containerRect = containerEl.getBoundingClientRect();
    const shipRect = shipEl.getBoundingClientRect();
    if (containerRect.width <= 0 || containerRect.height <= 0) {
      setLocalBurn(false);
      setTravelPhase('idle');
      setGlideTargetId(null);
      return;
    }

    // Live on-screen position (mid-transition). Do NOT write this back as
    // style.left first — that would reverse-animate. Changing the end target
    // below retargets the running CSS transition from the current computed
    // point, which is what preserves momentum into the halt.
    let live: PctPoint = {
      xPct: ((shipRect.left + shipRect.width / 2 - containerRect.left) / containerRect.width) * 100,
      yPct: ((shipRect.top + shipRect.height / 2 - containerRect.top) / containerRect.height) * 100,
    };

    let dx = dest.xPct - live.xPct;
    let dy = dest.yPct - live.yPct;
    let remaining = Math.hypot(dx, dy);
    // jsdom (and some instant layout paths) report the ship already at the
    // style end-target mid-flight. If we're still in a powered/coast phase,
    // synthesize a mid-course point from the recorded origin so Halt still
    // has momentum to bleed.
    const origin = travelOriginRef.current;
    if (remaining < 0.4 && origin && (phase === 'accelerating' || phase === 'gliding')) {
      live = {
        xPct: origin.xPct + (dest.xPct - origin.xPct) * 0.45,
        yPct: origin.yPct + (dest.yPct - origin.yPct) * 0.45,
      };
      dx = dest.xPct - live.xPct;
      dy = dest.yPct - live.yPct;
      remaining = Math.hypot(dx, dy);
    }
    if (remaining < 0.4) {
      setLocalBurn(false);
      setTravelPhase('idle');
      setGlideTargetId(null);
      return;
    }

    const ux = dx / remaining;
    const uy = dy / remaining;
    const coastAhead = Math.min(remaining * 0.9, Math.max(2.5, remaining * TRAVEL_HALT_COAST_FRAC));
    const stopPoint: PctPoint = {
      xPct: clampPct(live.xPct + ux * coastAhead),
      yPct: clampPct(live.yPct + uy * coastAhead),
    };

    const travelHdg = headingDeg(live, stopPoint, bandAspect);
    const prograde = headingRef.current + shortestAngleDelta(headingRef.current, travelHdg);
    const retrograde = prograde + 180;

    // Keep glideTargetId so APPROACH→HALT UI stays coherent until we park.
    setLocalBurn(false);
    setTravelPhase('halt-turn');
    setHeading(retrograde);
    // Retarget the continuous glide to a nearby stop — browser continues from
    // the live interpolated position with the new (short) halt duration.
    setShipPos(stopPoint);
    shipPosRef.current = stopPoint;

    const timers = travelTimersRef.current;
    timers.push(setTimeout(() => {
      setTravelPhase('halt-brake');
      setLocalBurn(true);
    }, TRAVEL_HALT_FLIP_MS));
    timers.push(setTimeout(() => {
      setLocalBurn(false);
      setTravelPhase('idle');
      setGlideTargetId(null);
    }, TRAVEL_HALT_FLIP_MS + TRAVEL_HALT_BRAKE_MS));
  }, [flight.stopSignal, clearTravelTimers, bandAspect, commitIspHalt]);

  useEffect(() => () => clearTravelTimers(), [clearTravelTimers]);

  // ---- Popups (click → info card, reusing the .ssv-popup glass) ----
  const openPopup = useCallback((meta: HitMeta, name: string, pos: PctPoint, objectId: string | null = null) => {
    setCtxMenu(null); // a left-click popup and a right-click menu are mutually exclusive overlays
    setPopup({ key: `${meta.kind}:${name}`, meta, name, xPct: pos.xPct, yPct: pos.yPct });
    // Inspecting a station/planet must not silently start movement. Their
    // popups own the explicit APPROACH → HALT → DOCK/LAND proximity flow.
    // Other object kinds keep the existing click-to-glide behavior.
    if (meta.kind !== 'station' && meta.kind !== 'planet') travelTo(pos, objectId);
  }, [travelTo]);

  // FIX C revise (Max correction: "no longer able to right click anywhere
  // and travel there" was fixed as DIRECT travel-on-right-click first, but
  // Max wants it MENU-mediated -- right-click opens a small "Travel To"
  // menu at the click point and the ship does NOT move until that item is
  // explicitly chosen). preventDefault still suppresses the native browser
  // menu; only the STASHED target + the immediate travel are new. Closes
  // any open left-click popup too (mutually exclusive overlays).
  const handleContextMenu = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const xPct = Math.min(100, Math.max(0, ((e.clientX - rect.left) / rect.width) * 100));
    const yPct = Math.min(100, Math.max(0, ((e.clientY - rect.top) / rect.height) * 100));
    setPopup(null);
    setCtxMenu({ xPct, yPct });
  }, []);

  // The menu's own "Travel To" click -- the ONLY place the stashed ctxMenu
  // target actually turns into a glide. Reuses the SAME travelTo() every
  // other glide entry point uses (left-click popups, a SOLAR row's
  // APPROACH, the old direct-travel cut) -- heading/burning/flight-context
  // wiring stays identical, not forked. `null` objectId matches travelTo's
  // own "no glide target" idiom (no specific body/station was targeted).
  const handleTravelToClick = useCallback(() => {
    if (!ctxMenu) return;
    travelTo({ xPct: ctxMenu.xPct, yPct: ctxMenu.yPct }, null);
    setCtxMenu(null);
  }, [ctxMenu, travelTo]);

  // Dismiss on outside-click or Escape -- standard floating-menu idiom.
  useEffect(() => {
    if (!ctxMenu) return;
    const onPointerDown = (e: MouseEvent) => {
      if (ctxMenuRef.current && !ctxMenuRef.current.contains(e.target as Node)) {
        setCtxMenu(null);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCtxMenu(null);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [ctxMenu]);

  const popupStyle = useMemo((): React.CSSProperties | null => {
    if (!popup || !containerRef.current) return { left: 8, top: 8 };
    const rect = containerRef.current.getBoundingClientRect();
    const px = (popup.xPct / 100) * rect.width;
    const py = (popup.yPct / 100) * rect.height;
    const left = Math.min(Math.max(6, px + 14), Math.max(6, rect.width - POPUP_W - 6));
    const top = Math.min(Math.max(6, py - POPUP_H / 2), Math.max(6, rect.height - POPUP_H - 6));
    return { left, top };
  }, [popup]);

  // Same clamped-anchor idiom as popupStyle above, sized for the smaller menu.
  const ctxMenuStyle = useMemo((): React.CSSProperties | null => {
    if (!ctxMenu || !containerRef.current) return { left: 8, top: 8 };
    const rect = containerRef.current.getBoundingClientRect();
    const px = (ctxMenu.xPct / 100) * rect.width;
    const py = (ctxMenu.yPct / 100) * rect.height;
    const left = Math.min(Math.max(6, px), Math.max(6, rect.width - CTXMENU_W - 6));
    const top = Math.min(Math.max(6, py), Math.max(6, rect.height - CTXMENU_H - 6));
    return { left, top };
  }, [ctxMenu]);

  const renderPopupContent = (): React.ReactNode => {
    if (!popup) return null;
    const meta = popup.meta;
    switch (meta.kind) {
      case 'star':
        return (
          <>
            <div className="ssv-popup-title">{meta.label.toUpperCase()}</div>
            <div className="ssv-popup-line">
              <span className="ssv-popup-swatch" style={{ background: meta.color }} aria-hidden="true"></span>
              CLASS {meta.starClass}
            </div>
            <div className="ssv-popup-line">PRIMARY — SECTOR {sectorId}</div>
          </>
        );
      case 'procedural':
        return (
          <>
            <div className="ssv-popup-title proc">{meta.designation}</div>
            <div className="ssv-popup-line proc">{meta.typeName}</div>
            <div className="ssv-popup-line proc">{meta.sizeDesc}</div>
            <div className="ssv-popup-status">UNSURVEYED — NO LANDING SITE</div>
          </>
        );
      case 'planet': {
        const ownerName = meta.owned
          ? planets.find((p) => p.id === meta.planetId)?.owner_name || 'CLAIMED'
          : null;
        const body = system?.bodies.find((b) => b.planet_id === meta.planetId);
        const planetPos = body
          ? bodyPosition(star, body, safeRadiiPlanets)
          : { xPct: popup.xPct, yPct: popup.yPct };
        const withinLandRange = Boolean(
          shipPos &&
          bandBox &&
          distancePx(shipPos, planetPos, bandBox) <= DOCK_RANGE_EM * bandBox.remPx
        );
        const approachingThisPlanet = localTraveling && glideTargetId === meta.planetId;
        return (
          <>
            <div className="ssv-popup-title">{popup.name.toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.planetKind.replace(/_/g, ' ').toUpperCase()}</div>
            {typeof meta.habitability === 'number' && (
              <div className="ssv-popup-line">HABITABILITY {Math.round(meta.habitability)}%</div>
            )}
            {ownerName && <div className="ssv-popup-line">OWNER — {ownerName}</div>}
            {approachingThisPlanet ? (
              <button
                type="button"
                className="ssv-popup-action halt"
                onClick={() => flight.allStop()}
                aria-label={`Halt approach to ${popup.name}`}
              >
                🛑 HALT
              </button>
            ) : withinLandRange && onRequestLand ? (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => { setPopup(null); onRequestLand(meta.planetId); }}
              >
                🛬 LAND
              </button>
            ) : body ? (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => travelTo(planetPos, meta.planetId)}
              >
                ➤ APPROACH
              </button>
            ) : null}
            {!withinLandRange && !approachingThisPlanet && (
              <div className="ssv-popup-status">OUTSIDE LANDING RANGE</div>
            )}
          </>
        );
      }
      case 'station': {
        const station = system?.stations.find((s) => s.station_id === meta.stationId);
        const stationPos = station
          ? stationPosition(star, station, safeRadiiStations)
          : { xPct: popup.xPct, yPct: popup.yPct };
        const withinDockRange = Boolean(
          shipPos &&
          bandBox &&
          distancePx(shipPos, stationPos, bandBox) <= DOCK_RANGE_EM * bandBox.remPx
        );
        const approachingThisStation = localTraveling && glideTargetId === meta.stationId;
        return (
          <>
            <div className="ssv-popup-title">{popup.name.toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.stationType.replace(/_/g, ' ').toUpperCase()}</div>
            {approachingThisStation ? (
              <button
                type="button"
                className="ssv-popup-action halt"
                onClick={() => flight.allStop()}
                aria-label={`Halt approach to ${popup.name}`}
              >
                🛑 HALT
              </button>
            ) : withinDockRange && onRequestDock ? (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => { setPopup(null); onRequestDock(meta.stationId); }}
              >
                ⚓ DOCK
              </button>
            ) : station ? (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => approachStation(station, stationPos)}
              >
                ➤ APPROACH
              </button>
            ) : null}
            {!withinDockRange && !approachingThisStation && (
              <div className="ssv-popup-status">OUTSIDE DOCKING RANGE</div>
            )}
          </>
        );
      }
      case 'ship':
        return (
          <>
            <div className="ssv-popup-title">{meta.shipName.toUpperCase()}</div>
            <div className="ssv-popup-line">
              <span className="ssv-popup-swatch" style={{ background: meta.factionColor }} aria-hidden="true"></span>
              {meta.factionLabel}
            </div>
            <div className="ssv-popup-line">{meta.shipType.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.isNpc ? 'NPC' : 'PILOT'} — {meta.captain.toUpperCase()}</div>
            {meta.isNpc && (
              <div className="ssv-popup-status" style={{ color: meta.lawful ? '#ffb000' : '#00ff41' }}>
                {meta.lawful ? '⚑ LAWFUL TARGET' : '✋ PROTECTED — ATTACK IS A CRIME'}
              </div>
            )}
          </>
        );
      case 'wreck':
        return (
          <>
            <div className="ssv-popup-title proc">WRECKAGE</div>
            <div className="ssv-popup-line proc">{meta.shipType.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-line proc">CAUSE — {meta.cause.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-status">{meta.suspect ? 'SALVAGE FLAGGED — CAUTION' : 'UNCLAIMED SALVAGE'}</div>
          </>
        );
      case 'formation':
        return (
          <>
            <div className="ssv-popup-title">{(meta.name || 'UNIDENTIFIED ANOMALY').toUpperCase()}</div>
            <div className="ssv-popup-line">{(meta.type || 'FORMATION').replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-status">{meta.discovered ? 'DISCOVERED' : 'UNDISCOVERED — SCAN TO CONFIRM'}</div>
          </>
        );
      default:
        return null;
    }
  };

  if (fetchFailed) {
    return (
      <div ref={containerRef} className="ssv-tableau">
        <div className="scene space">
          <div className="stars" />
          <div style={{
            position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
            color: 'rgba(0,217,255,0.32)', fontSize: '0.75em', letterSpacing: '.06em',
          }}>
            SCAN ACQUISITION FAILED
          </div>
        </div>
      </div>
    );
  }

  const hasNebula = !!system?.nebula;
  const hasHazard = hazardLevel >= 5;
  const selectedShip = ships.find((s) => s.ship_id && String(s.ship_id) === String(selectedShipId));
  const selectedPos = (() => {
    if (!selectedShip?.ship_id) return null;
    if (selectedShip.pose) {
      const s = deriveIspPose(selectedShip.pose as IspPose, ispNowMs());
      return { xPct: s.x_pct, yPct: s.y_pct };
    }
    const pose = otherShipFlightPose(String(selectedShip.ship_id), contactT, contactDocks, {
      archetype: selectedShip.archetype,
      activity: selectedShip.activity,
      mission: selectedShip.mission,
      bandAspect,
    });
    return { xPct: pose.xPct, yPct: pose.yPct };
  })();

  return (
    <div ref={containerRef} className="ssv-tableau" onContextMenu={handleContextMenu}>
      <div className={`scene space${hasNebula ? ' nebula' : ''}${hasHazard ? ' hazard' : ''}`}>
        <div className="stars" />

        {/* hazard bands — nebula haze + collision-debris ring, blurred SVG
            arcs along the star's orbital plane (not rings). */}
        {(hazeArcs.length > 0 || debrisRingArc) && (
          <svg className="hazard-arcs" viewBox="0 0 100 100" preserveAspectRatio="none">
            <defs>
              <filter id="ssv-hblur" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="1.1" />
              </filter>
            </defs>
            {hazeArcs.map((arc, i) => (
              <path
                key={`neb-${i}`}
                d={arcPath(star, arc)}
                stroke={`hsla(${system?.nebula?.hue ?? 260}, 70%, 55%, ${Math.min(0.4, Math.max(0.1, (system?.nebula?.density ?? 0.3) * 0.35))})`}
                strokeWidth={2.2}
                fill="none"
                strokeLinecap="round"
                filter="url(#ssv-hblur)"
              />
            ))}
            {debrisRingArc && system?.debris && (
              <path
                key="debris"
                d={arcPath(star, debrisRingArc)}
                stroke={`hsla(${system.debris.hue}, 30%, 45%, 0.4)`}
                strokeWidth={1.6}
                fill="none"
                strokeLinecap="round"
                filter="url(#ssv-hblur)"
              />
            )}
          </svg>
        )}

        {/* asteroid belt — decorative, mostly off-frame (the "sliver") */}
        {belt && (
          <div
            className="belt"
            style={{
              left: `${belt.xPct}%`, top: `${belt.yPct}%`,
              width: `${belt.wPct}%`, height: `${belt.hPct}%`,
              transform: 'translate(-50%,-50%)',
            }}
          />
        )}

        {/* the star */}
        {system?.star && (
          <>
            <button
              type="button"
              className="sun"
              style={{
                left: `${star.xPct}%`, top: `${star.yPct}%`,
                width: `${star.sizeEm}em`, height: `${star.sizeEm}em`,
                transform: 'translate(-50%,-50%)',
                background: `radial-gradient(circle at 38% 35%, #FFFFFF, ${system.star.color} 45%, transparent 78%)`,
                boxShadow: `0 0 3em ${system.star.color}66, 0 0 1em ${system.star.color}`,
              }}
              onClick={() =>
                system.star &&
                openPopup(
                  { kind: 'star', label: system.star.label, starClass: system.star.kind.replace(/_/g, ' '), color: system.star.color },
                  system.star.label || 'PRIMARY STAR',
                  star
                )
              }
              aria-label={system.star.label || 'Primary star'}
            />
            <div className="pltag" style={{ position: 'absolute', left: `${star.xPct}%`, top: `${star.yPct + 14}%`, transform: 'translateX(-50%)' }}>
              {system.star.kind.replace(/_/g, ' ')}
            </div>
          </>
        )}

        {/* planets + their moons */}
        {(system?.bodies ?? []).map((body) => {
          const pos = bodyPosition(star, body, safeRadiiPlanets);
          const sizeEm = bodySizeEm(body);
          const moons = moonOrbits(sectorId, body);
          const isReal = body.real && body.planet_id;
          // FIX A (Max live-playtest): decorative (non-real) bodies used to
          // show a fabricated `PROCEDURAL-${sectorId}-${idx}` designation,
          // discarding the REAL corpus name the server already generates for
          // every body slot (celestial_service.py's own generate_skeleton/
          // generate_system: `b["name"] = name_for_body(...)`, only
          // OVERWRITTEN — never cleared — for slots that get a real planet
          // merged over them). `name` here already carries that real value
          // (with a defensive `slot-N` fallback for the never-observed case
          // it's somehow empty) — use it directly for every body, real or
          // decorative, instead of fabricating a designation the server
          // already solved.
          const name = body.name || `slot-${body.slot}`;
          return (
            <React.Fragment key={`body-${body.slot}`}>
              {orbitEllipse(star, pos, `orbit-body-${body.slot}`)}
              <button
              type="button"
              className="pl"
              style={{
                left: `${pos.xPct}%`, top: `${pos.yPct}%`,
                width: `${sizeEm}em`, height: `${sizeEm}em`,
                // T1-A: the demo (RATIFIED.html L1222) centers `.pl` on its
                // own %-anchor via this same transform, matching every
                // sibling object (.sun/.obj/.other below) — WindshieldTableau
                // had dropped it, so a body's box was anchored by its
                // TOP-LEFT corner instead of its center, silently biasing
                // every rendered disc a further half-diameter down-right of
                // its intended position (compounding the out-of-band overflow
                // this WO fixes, not just decorative).
                transform: 'translate(-50%,-50%)',
                background: `hsl(${body.palette.hue}, ${body.palette.sat}%, 45%)`,
              }}
              aria-label={name}
              onClick={() =>
                isReal
                  ? openPopup(
                      { kind: 'planet', planetId: body.planet_id as string, planetKind: body.kind, habitability: body.habitability, owned: body.owned },
                      name,
                      pos,
                      body.planet_id as string
                    )
                  : openPopup(
                      { kind: 'procedural', designation: name, typeName: body.kind.replace(/_/g, ' '), sizeDesc: `SIZE CLASS ${body.size_class}` },
                      name,
                      pos
                    )
              }
            >
              <span className={`pltag${isReal && body.habitability ? '' : ' dim'}`}>
                {name}{isReal && !body.habitability ? ' ◦' : ''}
              </span>
              {moons.map((m, mi) => (
                <span
                  key={`moon-${mi}`}
                  className={`moon-orbit${m.clockwise ? '' : ' ccw'}`}
                  style={{
                    animationDuration: `${m.durationS}s`,
                    // Negative delay = the standard CSS trick for a seeded
                    // starting phase on a looping animation without a jump
                    // discontinuity at each loop restart (an inline
                    // `transform` would fight the keyframe's own `from`).
                    animationDelay: `${-(m.startDeg / 360) * m.durationS}s`,
                  }}
                  aria-hidden="true"
                >
                  <span
                    className="moon-dot"
                    style={{ left: `${m.radiusEm}em`, top: 0, width: `${m.sizeEm}em`, height: `${m.sizeEm}em` }}
                  />
                </span>
              ))}
              </button>
            </React.Fragment>
          );
        })}

        {/* stations */}
        {(system?.stations ?? []).map((st) => {
          const pos = stationPosition(star, st, safeRadiiStations);
          return (
            <React.Fragment key={`station-${st.station_id}`}>
              {orbitEllipse(star, pos, `orbit-station-${st.station_id}`)}
              <button
                type="button"
                className="obj"
                style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
                aria-label={st.name}
                onClick={() => openPopup({ kind: 'station', stationId: st.station_id, stationType: st.type }, st.name, pos, st.station_id)}
              >
                <span className="glyphbox">🛰</span>
                <span className="objtag">{st.name}</span>
              </button>
            </React.Fragment>
          );
        })}

        {/* SCAN layer — wrecks + formations, gated behind scanActive */}
        {scanActive && wrecks.map((w) => {
          const pos = scanPosition(w.id);
          return (
            <React.Fragment key={`wreck-${w.id}`}>
              {orbitEllipse(star, pos, `orbit-wreck-${w.id}`)}
              <button
                type="button"
                className="obj"
                style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)', background: 'none', border: 'none' }}
                aria-label={`Wreckage — ${w.destroyed_ship_type}`}
                onClick={() =>
                  openPopup(
                    { kind: 'wreck', wreckId: w.id, shipType: w.destroyed_ship_type, cause: w.cause, suspect: w.would_flag_suspect },
                    'WRECKAGE',
                    pos
                  )
                }
              >
                <svg viewBox="0 0 44 20" style={{ width: '1.9em', height: '.9em', display: 'block', transform: 'rotate(-11deg)', opacity: 0.5 }}>
                  <path d="M4 11 L15 6 L19 9 L10 14 Z" fill="#4A4038" stroke="#8A7A66" strokeWidth={0.7} />
                  <path d="M23 9 L34 4 L39 7 L28 13 Z" fill="#3E362E" stroke="#7A6A56" strokeWidth={0.7} transform="rotate(14 31 8)" />
                  <line x1="17" y1="9" x2="24" y2="8" stroke="#5A4E42" strokeWidth={0.6} strokeDasharray="1.5 1.5" />
                  <circle cx="14" cy="16" r={0.7} fill="#6E6254" />
                  <circle cx="30" cy="16" r={0.5} fill="#6E6254" />
                  <circle cx="38" cy="12" r={0.6} fill="#57493E" />
                  <circle cx="21" cy="4" r={0.5} fill="#8A7A66" />
                </svg>
                <span className="objtag">WRECK — SALVAGE</span>
              </button>
            </React.Fragment>
          );
        })}
        {scanActive && formations.map((f) => {
          const pos = scanPosition(f.id);
          const discovered = f.is_discovered;
          return discovered ? (
            <button
              key={`formation-${f.id}`}
              type="button"
              className="obj"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
              aria-label={f.name || 'Discovered anomaly'}
              onClick={() => openPopup({ kind: 'formation', formationId: f.id, name: f.name, type: f.type, discovered: true }, f.name || 'ANOMALY', pos)}
            >
              <span className="glyphbox" style={{ color: '#C9B8F5' }}>◇</span>
              <span className="objtag">{(f.name || 'DERELICT BEACON').toUpperCase()}</span>
            </button>
          ) : (
            <button
              key={`formation-${f.id}`}
              type="button"
              className="anom"
              style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%`, transform: 'translate(-50%,-50%)' }}
              aria-label="Unresolved signal"
              title="an unresolved flicker — fly to it"
              onClick={() => openPopup({ kind: 'formation', formationId: f.id, name: null, type: null, discovered: false }, 'UNIDENTIFIED ANOMALY', pos)}
            >
              ◇
            </button>
          );
        })}

        {/* other ships — prefer server ISP pose/leg; fall back to local flight
            profile until the sector presence carries pose (Loop A tick). */}
        {ships.map((s) => {
          if (!s.ship_id) return null;
          let xPct: number;
          let yPct: number;
          let headingDegVal: number;
          let burningContact = false;
          let phaseClass = 'idle';
          if (s.pose) {
            const sample = deriveIspPose(s.pose as IspPose, ispNowMs());
            xPct = sample.x_pct;
            yPct = sample.y_pct;
            headingDegVal = sample.heading_deg;
            burningContact = !!sample.burning;
            phaseClass = ispPhaseToTravelClass(String(sample.phase));
          } else {
            const pose = otherShipFlightPose(String(s.ship_id), contactT, contactDocks, {
              archetype: s.archetype,
              activity: s.activity,
              mission: s.mission,
              bandAspect,
            });
            xPct = pose.xPct;
            yPct = pose.yPct;
            headingDegVal = pose.headingDeg;
            burningContact = pose.burning;
            phaseClass = pose.phase === 'brake-turn' ? 'brake-turn'
              : pose.phase === 'final-orient' ? 'final-orient'
              : pose.phase;
          }
          const faction = shipFaction(s);
          const isPirate = faction.key === 'raider';
          const turning =
            phaseClass === 'orienting' ||
            phaseClass === 'brake-turn' ||
            phaseClass === 'final-orient' ||
            phaseClass === 'halt-turn';
          return (
            <button
              key={`ship-${s.ship_id}`}
              type="button"
              className={`other${burningContact ? ' burning' : ''}${phaseClass !== 'idle' ? ` travel-${phaseClass}` : ''}`}
              style={{
                left: `${xPct}%`,
                top: `${yPct}%`,
                color: faction.color,
                ['--hdg' as string]: `${headingDegVal.toFixed(0)}deg`,
              }}
              aria-label={`${s.ship_name || 'Contact'} options`}
              onClick={() =>
                openPopup(
                  {
                    kind: 'ship', shipId: String(s.ship_id), shipName: s.ship_name || 'UNKNOWN',
                    shipType: s.ship_type || 'UNKNOWN', captain: s.username || 'UNKNOWN',
                    isNpc: !!s.is_npc, factionLabel: faction.label, factionColor: faction.color,
                    lawful: faction.lawful, notoriety: s.notoriety ?? undefined,
                  },
                  s.ship_name || 'Contact',
                  { xPct, yPct }
                )
              }
            >
              <span className="other-hull" aria-hidden="true">
                {isPirate ? '☠' : '⊳'}
                {turning && (
                  <>
                    <span className="ssv-rcs ssv-rcs-a" />
                    <span className="ssv-rcs ssv-rcs-b" />
                  </>
                )}
              </span>
              <span className="pltag" style={{ color: faction.color }}>{s.ship_name || faction.label}</span>
            </button>
          );
        })}

        {/* target reticle */}
        {selectedPos && <div className="reticle" style={{ left: `${selectedPos.xPct}%`, top: `${selectedPos.yPct}%` }} />}

        {/* the player's own ship — the ONLY system-level mover */}
        {shipPos && (
          <div
            ref={shipMkRef}
            className={`shipmk${burning ? ' burning' : ''}${travelPhase !== 'idle' ? ` travel-${travelPhase}` : ''}${warpPhase === 'turning' ? ' warp-turning' : ''}${warpPhase === 'launch' ? ' warp-launching' : ''}${warpPhase === 'arriving' ? ' warp-arriving' : ''}`}
            style={{ left: `${shipPos.xPct}%`, top: `${shipPos.yPct}%`, '--hdg': `${heading.toFixed(0)}deg`, '--warp-bearing': `${warpBearing.toFixed(0)}deg`, '--arrival-bearing': `${arrivalBearing.toFixed(0)}deg` } as React.CSSProperties}
          >
            ➤
            {/* RCS attitude jets fire for every attitude change: initial local
                orientation, flip for braking, final facing, and pre-warp turn. */}
            {(warpPhase === 'turning' ||
              travelPhase === 'orienting' ||
              travelPhase === 'brake-turn' ||
              travelPhase === 'halt-turn' ||
              travelPhase === 'redirect-turn' ||
              travelPhase === 'final-orient') && (
              <>
                <span className="ssv-rcs ssv-rcs-a" aria-hidden="true" />
                <span className="ssv-rcs ssv-rcs-b" aria-hidden="true" />
              </>
            )}
          </div>
        )}

        {/* Warp cinematic — a spherical warp field that inflates around the
            hull (charging), snaps + streaks out along the exit bearing
            (launch), then a flash as the destination sector takes over
            (arriving). Anchored to the ship's live position. Purely decorative. */}
        {shipPos && warpPhase !== 'idle' && warpPhase !== 'turning' && (
          <div
            className={`ssv-warp warp-${warpPhase}`}
            style={{ left: `${shipPos.xPct}%`, top: `${shipPos.yPct}%`, '--warp-bearing': `${warpBearing.toFixed(0)}deg`, '--arrival-bearing': `${arrivalBearing.toFixed(0)}deg` } as React.CSSProperties}
            aria-hidden="true"
          >
            <span className="ssv-warp-bubble" />
            <span className="ssv-warp-streak" />
          </div>
        )}
        {(warpPhase === 'launch' || warpPhase === 'arriving') && (
          <div className="ssv-warp-flash" aria-hidden="true" />
        )}
      </div>

      {popup && popupStyle && (
        <div className="ssv-popup" style={popupStyle} role="dialog" aria-label={`${popup.name} details`}>
          <button type="button" className="ssv-popup-close" onClick={() => setPopup(null)} aria-label="Close details">✕</button>
          {renderPopupContent()}
        </div>
      )}

      {ctxMenu && ctxMenuStyle && (
        <div ref={ctxMenuRef} className="ssv-ctxmenu" style={ctxMenuStyle} role="menu" aria-label="Sector context menu">
          <button type="button" className="ssv-popup-action" role="menuitem" onClick={handleTravelToClick}>
            Travel To
          </button>
        </div>
      )}
    </div>
  );
};

export default WindshieldTableau;
