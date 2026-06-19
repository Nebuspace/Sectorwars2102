import React, { useEffect, useRef, useState } from 'react';
import apiClient from '../../services/apiClient';
import SectorViewport from './SectorViewport';
import './solar-system-viewscreen.css';

/**
 * SolarSystemViewscreen — the cockpit windshield spectacle.
 *
 * Renders the procedural solar system snapshot served by
 * GET /api/v1/sectors/{id}/system on a container-sized 2D canvas:
 * parallax starfield, nebula haze, a kind-differentiated star, orbit
 * arcs with perspective squash, per-kind painted planets (real planets
 * merged in as click targets), moons, rings, asteroid belts and
 * stations on stable orbits. Deterministic per sector: every visual
 * seed derives from the server snapshot (itself seeded by sector_id),
 * and orbital drift derives purely from the wall clock.
 *
 * On fetch failure it falls back to the legacy SectorViewport so the
 * viewscreen never goes dark.
 */

// ---------------------------------------------------------------------------
// Contract types (mirror the /sectors/{id}/system response shape)
// ---------------------------------------------------------------------------

interface SystemStarSecondary {
  kind: string;
  color: string;
}

interface SystemStar {
  kind: string;
  label: string;
  color: string;
  secondary?: SystemStarSecondary | null;
}

interface SystemNebula {
  hue: number;
  density: number;
}

interface SystemBelt {
  inner_au: number;
  outer_au: number;
}

/** Collision-debris ring: two worlds that collided long ago, their wreck
 *  spread into a belt encircling the orbital plane. An annulus like the
 *  asteroid belt (reddish). Cosmetic only — non-clickable. */
interface SystemDebris {
  inner_au: number;
  outer_au: number;
  hue: number;
}

/** Habitable zone band (inner/outer in normalized orbit-AU space). */
interface SystemHabitableZone {
  inner_au: number;
  outer_au: number;
}

interface SystemBody {
  slot: number;
  orbit_au: number;
  kind: string;
  size_class: number;
  palette: { hue: number; sat: number };
  rings: boolean;
  moons: number;
  phase_deg: number;
  /** Axial rotation (cosmetic): the world's local "day" length + obliquity,
   *  distinct from orbital revolution. From the SectorCelestial model; optional
   *  so older persisted skeletons (pre-rotation) fall back to a seed-derived
   *  value client-side. */
  rotation_period_hours?: number;
  axial_tilt_deg?: number;
  real: boolean;
  planet_id?: string;
  name?: string;
  habitability?: number;
  owned?: boolean;
  /** ADR-0073: true when the viewer is this planet's discoverer (may rename). */
  can_rename?: boolean;
  /** "forming" while genesis terraforming is in progress; "complete"/absent when done. */
  formation_status?: string;
}

interface SystemStation {
  station_id: string;
  name: string;
  type: string;
  orbit_au: number;
  phase_deg: number;
}

interface SystemSnapshot {
  sector_id: number;
  sector_type: string;
  star: SystemStar | null;
  extra_stars?: SystemStarSecondary[];
  nebula: SystemNebula | null;
  belt: SystemBelt | null;
  debris?: SystemDebris | null;
  habitable_zone?: SystemHabitableZone | null;
  bodies: SystemBody[];
  stations: SystemStation[];
}

interface SolarSystemViewscreenProps {
  sectorId: number;
  sectorType?: string;
  sectorName?: string;
  hazardLevel?: number;
  radiationLevel?: number;
  /** Legacy sector entities — only used by the SectorViewport fallback */
  stations?: any[];
  planets?: any[];
  /**
   * flight scene only: ships present in the sector (the dashboard's filtered
   * players_present — NPC captains and other pilots, excluding self). Rendered
   * as clickable glyphs in the foreground; each opens a contact popup.
   */
  ships?: any[];
  onEntityClick?: (entity: { type: 'station' | 'planet'; id: string; name: string }) => void;
  /**
   * Scene mode (GLASS LAW): the windshield band always hosts this same
   * canvas component, and the scene prop selects what it paints.
   *   flight (default) — the procedural solar-system spectacle
   *   docked           — station bay silhouette (no system fetch, no hit targets)
   *   landed           — planet-surface vista (no system fetch, no hit targets)
   */
  scene?: 'flight' | 'docked' | 'landed';
  /** docked scene only: tints the bay guide lights (blue) for SpaceDocks */
  isSpaceDock?: boolean;
  /** landed scene only: planet type drives the sky/ridge palette */
  planetType?: string;
  /** landed scene only: 0–100 habitability — drives flora vs desolation + star count */
  habitability?: number;
  /** landed scene only: 0–5 citadel level — drives the built skyline on the horizon */
  citadelLevel?: number;
  /**
   * landed scene only: the landed planet's id, used to locate THIS world's body
   * in the system snapshot for its orbit_au (distance-to-sun). Falls back to a
   * mid orbit (~0.5) when it can't be matched.
   */
  landedPlanetId?: string;
  /**
   * flight scene only: when provided, the real-planet info popup offers a
   * 🛬 LAND action that calls this with the planet id (wire to the same
   * helm land handler — it owns the helmBusy latch).
   */
  onRequestLand?: (planetId: string) => void;
  /**
   * flight scene only: when provided, the station info popup offers an
   * ⚓ DOCK action that calls this with the station id (wire to the same
   * helm dock handler — it owns the helmBusy latch).
   */
  onRequestDock?: (stationId: string) => void;
  /**
   * flight scene only: ship_id of the COMMS-selected contact. Its glyph gets a
   * pulsing selection reticle so clicking a contact in the Comms window
   * spotlights that ship in the main cockpit viewport.
   */
  selectedShipId?: string | null;
}

/** Per-kind payload backing the click popup card */
type HitMeta =
  | { kind: 'star'; label: string; starClass: string; color: string }
  | { kind: 'planet'; planetId: string; planetKind: string; habitability?: number; owned?: boolean }
  | { kind: 'station'; stationId: string; stationType: string }
  | { kind: 'procedural'; designation: string; typeName: string; sizeDesc: string }
  | { kind: 'ship'; shipId: string; shipName: string; shipType: string; captain: string;
      isNpc: boolean; factionLabel: string; factionColor: string; lawful: boolean;
      notoriety?: number };

interface HitTarget {
  /** screen-space hit data in CSS pixels (the draw loop paints through a
      setTransform(dpr, …) so every recorded coordinate is CSS-pixel space) */
  x: number;
  y: number;
  r: number;
  kind: 'star' | 'planet' | 'station' | 'procedural' | 'ship';
  id?: string;
  name: string;
  lines: string[];
  meta: HitMeta;
}

/** Sector ship presence (subset of players_present the dashboard passes). */
interface ShipPresence {
  player_id?: string;
  user_id?: string;
  username?: string;
  ship_id?: string;
  ship_name?: string;
  ship_type?: string;
  is_npc?: boolean;
  team_id?: string | null;
  /** Authoritative NPC archetype (LAW_ENFORCEMENT | HOSTILE_RAIDER | TRADER). */
  archetype?: string | null;
  /** Trader scruples 0–100: low = reputable, ≥50 = unscrupulous (fair game). */
  notoriety?: number | null;
  /** Live activity (COMMUTE | WORK_STATION | PATROL | …) — drives honest motion. */
  activity?: string | null;
  /** Trader mission (commerce | colonist | science) — drives which dock type. */
  mission?: string | null;
}

/** Faction read of a ship — drives glyph color + label. Uses the authoritative
 *  archetype when present (falls back to ship_type/name); traders are further
 *  graded by notoriety so a paladin can tell an honest merchant (green) from a
 *  shady one (amber) or a notorious smuggler (orange) at a glance. */
function shipFaction(s: ShipPresence): { key: string; color: string; label: string; lawful: boolean } {
  if (!s.is_npc) return { key: 'pilot', color: '#00d9ff', label: 'PILOT', lawful: false };
  const arch = (s.archetype || '').toUpperCase();
  const tp = (s.ship_type || '').toUpperCase();
  const nm = (s.ship_name || '').toUpperCase();
  const isLaw = arch === 'LAW_ENFORCEMENT'
    || tp.includes('MARSHAL') || tp.includes('SENTINEL') || tp.includes('INTERDICTOR');
  if (isLaw) return { key: 'law', color: '#5b8dff', label: 'LAW ENFORCEMENT', lawful: false };
  const isRaider = arch === 'HOSTILE_RAIDER' || nm.includes('MARAUDER') || tp.includes('PIRATE');
  if (isRaider) return { key: 'raider', color: '#ff5a5a', label: 'HOSTILE', lawful: true };
  // Trader — grade by notoriety
  const n = typeof s.notoriety === 'number' ? s.notoriety : 0;
  if (n >= 75) return { key: 'notorious', color: '#ff7a3c', label: 'NOTORIOUS TRADER', lawful: true };
  if (n >= 50) return { key: 'unscrupulous', color: '#ffb000', label: 'UNSCRUPULOUS TRADER', lawful: true };
  if (n >= 25) return { key: 'merchant', color: '#7fe0a0', label: 'MERCHANT', lawful: false };
  return { key: 'reputable', color: '#00ff41', label: 'REPUTABLE MERCHANT', lawful: false };
}

interface PopupState {
  /** identity of the body the popup is anchored to (kind:id-or-name) */
  key: string;
  target: HitTarget;
  /** clamped CSS-pixel position inside the windshield band */
  left: number;
  top: number;
}

const popupKeyFor = (t: HitTarget): string => `${t.kind}:${t.id ?? t.name}`;

// Popup card footprint used for clamping fully inside the band
const POPUP_W = 232;
const POPUP_H = 158;

// ---------------------------------------------------------------------------
// Deterministic PRNG (splitmix32) — every visual seed flows through this
// ---------------------------------------------------------------------------

function splitmix32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x9e3779b9) >>> 0;
    let t = s ^ (s >>> 16);
    t = Math.imul(t, 0x21f0aaad);
    t = t ^ (t >>> 15);
    t = Math.imul(t, 0x735a2d97);
    return ((t ^ (t >>> 15)) >>> 0) / 4294967296;
  };
}

function hexToRgb(c: string | undefined): { r: number; g: number; b: number } {
  const m = /^#?([0-9a-f]{6})$/i.exec(c || '');
  if (!m) return { r: 255, g: 240, b: 220 };
  const n = parseInt(m[1], 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

const CYAN = '#00d9ff';
const PROC_GREY = 'rgba(158, 150, 184, 0.85)';
const FONT = '10px "Courier New", monospace';

// Vertical squash applied to every orbital ellipse (slight perspective)
const SQUASH = 0.35;

// Pacing knobs — calm but ALIVE. Decoupled so planets visibly orbit the sun
// while moons/ships stay unhurried (Max: planet orbit was "slightly too slow").
// Ambient cadence — starfield parallax, station blink, hazard pulse — keeps its
// own timing; these scale "the rotation/motion" only.
const ORBIT_SCALE = 0.85;  // planets + stations orbiting the star (perceptible)
const MOON_SCALE = 0.4;    // moons spinning around their planet (gentle)
const SHIP_SCALE = 0.6;    // ship transit / drift between objects
const SPIN_SCALE = 0.5;    // planets rotating on their own axis (calm, per-planet rate)
// Back-compat alias for the belt/debris churn (kept calm).
const MOTION_SCALE = MOON_SCALE;

/** A moon's screen position on its planet's SINGLE shared orbital plane.
 *  Every moon of a body rides the same tilted/foreshortened ellipse (one
 *  inclination per planet) so they read as a coplanar system rather than each
 *  spinning on its own axis. `depth` (>0 in front of the planet, <0 behind)
 *  lets the caller dim the far side for a 3-D read. */
function moonPlanePos(
  cx: number, cy: number, radius: number, ang: number,
  tilt: number, squash: number
): { x: number; y: number; depth: number } {
  const ex = Math.cos(ang) * radius;
  const ey = Math.sin(ang) * radius * squash;
  return {
    x: cx + ex * Math.cos(tilt) - ey * Math.sin(tilt),
    y: cy + ex * Math.sin(tilt) + ey * Math.cos(tilt),
    depth: Math.sin(ang),
  };
}

// ---------------------------------------------------------------------------
// Star rendering
// ---------------------------------------------------------------------------

const STAR_RADIUS_FACTOR: Record<string, number> = {
  M_DWARF: 0.05,
  K_ORANGE: 0.06,
  G_YELLOW: 0.07,
  F_WHITE: 0.075,
  A_BLUE: 0.085,
  B_BLUE_GIANT: 0.115,
  O_BLUE_SUPER: 0.145,
  RED_GIANT: 0.16,
  WHITE_DWARF: 0.024,
  NEUTRON: 0.014,
  BLACK_HOLE: 0.05
};

function starRadius(kind: string, w: number, h: number): number {
  const f = STAR_RADIUS_FACTOR[kind] ?? 0.07;
  return Math.min(w, h) * f;
}

function drawStar(
  ctx: CanvasRenderingContext2D,
  kind: string,
  color: string,
  x: number,
  y: number,
  r: number,
  w: number,
  h: number
): void {
  const { r: cr, g: cg, b: cb } = hexToRgb(color);

  if (kind === 'BLACK_HOLE') {
    // Accretion disk (additive, squashed, tilted)
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.beginPath();
    ctx.ellipse(x, y, r * 2.3, r * 0.75, -0.25, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255, 150, 60, 0.45)';
    ctx.lineWidth = r * 0.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(x, y, r * 1.7, r * 0.5, -0.25, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255, 232, 200, 0.7)';
    ctx.lineWidth = r * 0.16;
    ctx.stroke();
    ctx.restore();
    // Event horizon — pure dark disk
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = '#03040a';
    ctx.fill();
    // Thin photon ring
    ctx.beginPath();
    ctx.arc(x, y, r * 1.08, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255, 246, 230, 0.85)';
    ctx.lineWidth = 1.2;
    ctx.stroke();
    return;
  }

  // Corona glow
  const coronaScale = kind === 'WHITE_DWARF' || kind === 'NEUTRON' ? 5 : 3;
  const corona = ctx.createRadialGradient(x, y, r * 0.4, x, y, r * coronaScale);
  corona.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, 0.55)`);
  corona.addColorStop(0.5, `rgba(${cr}, ${cg}, ${cb}, 0.14)`);
  corona.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
  ctx.fillStyle = corona;
  ctx.beginPath();
  ctx.arc(x, y, r * coronaScale, 0, Math.PI * 2);
  ctx.fill();

  if (kind === 'NEUTRON') {
    // Faint beam cross
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const beam = Math.min(w, h) * 0.32;
    for (const ang of [0.45, 0.45 + Math.PI / 2]) {
      const dx = Math.cos(ang) * beam;
      const dy = Math.sin(ang) * beam;
      const grad = ctx.createLinearGradient(x - dx, y - dy, x + dx, y + dy);
      grad.addColorStop(0, 'rgba(200, 230, 255, 0)');
      grad.addColorStop(0.5, 'rgba(220, 240, 255, 0.35)');
      grad.addColorStop(1, 'rgba(200, 230, 255, 0)');
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(x - dx, y - dy);
      ctx.lineTo(x + dx, y + dy);
      ctx.stroke();
    }
    ctx.restore();
  }

  // Stellar disk
  const disk = ctx.createRadialGradient(x - r * 0.25, y - r * 0.25, r * 0.05, x, y, r);
  disk.addColorStop(0, '#ffffff');
  disk.addColorStop(0.45, `rgb(${Math.min(255, cr + 60)}, ${Math.min(255, cg + 60)}, ${Math.min(255, cb + 60)})`);
  disk.addColorStop(1, color);
  ctx.fillStyle = disk;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();

  if (kind === 'WHITE_DWARF') {
    // Hard, tight glow ring
    ctx.beginPath();
    ctx.arc(x, y, r * 1.6, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.6)`;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

// ---------------------------------------------------------------------------
// Planet surface treatments — the variety is the feature
// ---------------------------------------------------------------------------

type Treatment = 'GAS_GIANT' | 'BARREN' | 'MOUNTAINOUS' | 'ICE' | 'VOLCANIC' | 'DESERT' | 'TERRAN' | 'OCEANIC';

function treatmentFor(kind: string): Treatment {
  const k = (kind || '').toUpperCase().replace('PLANETTYPE.', '');
  switch (k) {
    case 'GAS_GIANT': return 'GAS_GIANT';
    case 'ICE': case 'FROZEN': case 'ARCTIC': case 'C_CLASS': return 'ICE';
    case 'VOLCANIC': case 'H_CLASS': return 'VOLCANIC';
    case 'DESERT': case 'K_CLASS': return 'DESERT';
    case 'TERRAN': case 'TERRA': case 'TROPICAL': case 'M_CLASS': case 'JUNGLE': return 'TERRAN';
    case 'OCEANIC': case 'O_CLASS': return 'OCEANIC';
    default: return 'BARREN'; // BARREN, MOUNTAINOUS, D_CLASS, unknown
  }
}

const PROC_FLAVOR: Record<Treatment, string> = {
  GAS_GIANT: 'GAS GIANT — UNINHABITABLE',
  BARREN: 'BARREN WORLD — UNINHABITABLE',
  MOUNTAINOUS: 'MOUNTAINOUS WORLD — MARGINAL',
  ICE: 'ICE WORLD — UNINHABITABLE',
  VOLCANIC: 'VOLCANIC WORLD — UNINHABITABLE',
  DESERT: 'DESERT WORLD — UNINHABITABLE',
  TERRAN: 'TERRAN WORLD — HABITABLE',
  OCEANIC: 'OCEANIC WORLD — HABITABLE'
};

// PlanetType values that genuinely render as the BARREN treatment (dead rock).
// Anything else falling through to BARREN is an unknown kind and should read
// as uncharted rather than being mislabeled a confirmed barren world.
const KNOWN_BARREN = new Set(['BARREN', 'MOUNTAINOUS', 'D_CLASS', 'ROCKY']);

function flavorFor(kind: string): string {
  const treatment = treatmentFor(kind);
  if (treatment === 'BARREN') {
    const k = (kind || '').toUpperCase().replace('PLANETTYPE.', '');
    if (!KNOWN_BARREN.has(k)) return 'UNCHARTED WORLD — NO LANDING BEACON';
  }
  return PROC_FLAVOR[treatment];
}

/** Popup type/palette name — the flavor line's leading clause (e.g. ICE WORLD). */
const typeNameFor = (kind: string): string => flavorFor(kind).split(' — ')[0];

/** Relative size descriptor for procedural worlds, from the snapshot's size_class. */
function sizeDescriptorFor(sizeClass: number): string {
  if (sizeClass <= 1) return 'MINOR BODY';
  if (sizeClass <= 3) return 'MID-SIZE WORLD';
  return 'GIANT WORLD';
}

/** Generated designation for composer-only background worlds: <sector>-<letter>. */
const proceduralDesignation = (sectorId: number, index: number): string =>
  `${sectorId}-${String.fromCharCode(65 + (index % 26))}`;

/** Paint the body surface (clipped to the disk), then terminator + rim light. */
function drawPlanetSurface(
  ctx: CanvasRenderingContext2D,
  body: SystemBody,
  x: number,
  y: number,
  r: number,
  starX: number,
  starY: number,
  seed: number,
  t: number
): void {
  const rng = splitmix32(seed);
  const hue = body.palette.hue;
  const sat = body.palette.sat;
  const treatment = treatmentFor(body.kind);

  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.clip();

  // --- Axial rotation: spin the surface beneath the (fixed) day/night
  // lighting so the world visibly turns on its own tilted axis. The rate is
  // per-planet (from rotation_period_hours — gas giants fast, big worlds slow),
  // so no two worlds spin in lockstep; axial_tilt_deg skews the spin axis. Falls
  // back to a seed-derived value for older skeletons that predate the fields.
  const rotH = body.rotation_period_hours ?? (12 + (seed % 36));
  const tiltRad = ((body.axial_tilt_deg ?? (seed % 46)) * Math.PI) / 180;
  const spin = (t * SPIN_SCALE * Math.PI * 2) / Math.max(1, rotH * 4);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(tiltRad + spin);
  ctx.translate(-x, -y);

  switch (treatment) {
    case 'GAS_GIANT': {
      ctx.fillStyle = `hsl(${hue}, ${sat}%, 38%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      const bands = 3 + Math.floor(rng() * 4); // 3-6 bands
      const bandH = (r * 2) / bands;
      for (let i = 0; i < bands; i++) {
        const hueShift = (rng() - 0.5) * 34;
        const light = 30 + rng() * 22;
        ctx.fillStyle = `hsla(${hue + hueShift}, ${sat}%, ${light}%, 0.8)`;
        ctx.fillRect(x - r, y - r + i * bandH, r * 2, bandH * (0.7 + rng() * 0.3));
      }
      if (rng() < 0.45) {
        // Oval storm spot
        ctx.beginPath();
        ctx.ellipse(
          x + (rng() - 0.5) * r * 1.1,
          y + (rng() - 0.5) * r * 0.9,
          r * 0.24, r * 0.12, 0.15, 0, Math.PI * 2
        );
        ctx.fillStyle = `hsla(${hue + 25}, ${Math.min(100, sat + 12)}%, 62%, 0.85)`;
        ctx.fill();
      }
      break;
    }
    case 'BARREN': {
      ctx.fillStyle = `hsl(${hue}, ${Math.round(sat * 0.35)}%, 36%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      const craters = 8 + Math.floor(rng() * 9);
      for (let i = 0; i < craters; i++) {
        const a = rng() * Math.PI * 2;
        const d = rng() * r * 0.85;
        const cr2 = r * (0.05 + rng() * 0.11);
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, cr2, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${hue}, ${Math.round(sat * 0.3)}%, ${18 + rng() * 10}%, 0.7)`;
        ctx.fill();
      }
      break;
    }
    case 'ICE': {
      ctx.fillStyle = `hsl(${hue}, ${Math.round(sat * 0.5)}%, 76%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Brighter polar caps
      ctx.fillStyle = 'rgba(255, 255, 255, 0.75)';
      ctx.beginPath();
      ctx.ellipse(x, y - r * 0.82, r * 0.7, r * 0.3, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(x, y + r * 0.82, r * 0.7, r * 0.3, 0, 0, Math.PI * 2);
      ctx.fill();
      // Faint crack lines
      const cracks = 3 + Math.floor(rng() * 3);
      ctx.strokeStyle = `hsla(${hue}, 45%, 52%, 0.5)`;
      ctx.lineWidth = Math.max(0.6, r * 0.04);
      for (let i = 0; i < cracks; i++) {
        let cx = x + (rng() - 0.5) * r;
        let cy = y + (rng() - 0.5) * r;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        const segs = 3 + Math.floor(rng() * 2);
        for (let s = 0; s < segs; s++) {
          cx += (rng() - 0.5) * r * 0.8;
          cy += (rng() - 0.5) * r * 0.5;
          ctx.lineTo(cx, cy);
        }
        ctx.stroke();
      }
      break;
    }
    case 'VOLCANIC': {
      ctx.fillStyle = `hsl(${hue}, 30%, 13%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Glowing fissures (additive)
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const fissures = 3 + Math.floor(rng() * 4);
      ctx.lineWidth = Math.max(0.7, r * 0.05);
      for (let i = 0; i < fissures; i++) {
        let fx = x + (rng() - 0.5) * r * 1.2;
        let fy = y + (rng() - 0.5) * r * 1.2;
        ctx.strokeStyle = `hsla(${14 + rng() * 14}, 95%, ${48 + rng() * 14}%, 0.85)`;
        ctx.beginPath();
        ctx.moveTo(fx, fy);
        const segs = 3 + Math.floor(rng() * 2);
        for (let s = 0; s < segs; s++) {
          fx += (rng() - 0.5) * r * 0.7;
          fy += (rng() - 0.5) * r * 0.6;
          ctx.lineTo(fx, fy);
        }
        ctx.stroke();
      }
      // Ember glow haze
      const ember = ctx.createRadialGradient(x, y, r * 0.1, x, y, r);
      ember.addColorStop(0, 'rgba(255, 90, 20, 0.18)');
      ember.addColorStop(1, 'rgba(255, 60, 0, 0)');
      ctx.fillStyle = ember;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      ctx.restore();
      break;
    }
    case 'DESERT': {
      ctx.fillStyle = `hsl(${hue}, ${sat}%, 54%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Warm dune bands
      const dunes = 3 + Math.floor(rng() * 2);
      const dh = (r * 2) / dunes;
      for (let i = 0; i < dunes; i++) {
        ctx.fillStyle = `hsla(${hue + (rng() - 0.5) * 16}, ${sat}%, ${44 + rng() * 18}%, 0.45)`;
        ctx.fillRect(x - r, y - r + i * dh + (rng() - 0.5) * dh * 0.3, r * 2, dh * 0.6);
      }
      // Lighter mottling
      const motts = 6 + Math.floor(rng() * 6);
      for (let i = 0; i < motts; i++) {
        const a = rng() * Math.PI * 2;
        const d = rng() * r * 0.8;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.04 + rng() * 0.07), 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${hue}, ${Math.round(sat * 0.7)}%, 70%, 0.5)`;
        ctx.fill();
      }
      break;
    }
    case 'TERRAN': {
      // Living world — ocean base, green continents, cloud flecks.
      // Modulate the ocean hue/lightness a few degrees off palette.hue so no
      // two living worlds read identically.
      const tHue = 208 + ((hue % 24) - 12) * 0.5;
      const tLight = 38 + (rng() - 0.5) * 6;
      ctx.fillStyle = `hsl(${tHue}, 64%, ${tLight}%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      const continents = 4 + Math.floor(rng() * 4);
      for (let i = 0; i < continents; i++) {
        const a = rng() * Math.PI * 2;
        const d = rng() * r * 0.75;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.14 + rng() * 0.2), 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${110 + rng() * 30}, 42%, ${30 + rng() * 12}%, 0.9)`;
        ctx.fill();
      }
      const clouds = 5 + Math.floor(rng() * 5);
      for (let i = 0; i < clouds; i++) {
        const a = rng() * Math.PI * 2;
        const d = rng() * r * 0.85;
        ctx.beginPath();
        ctx.ellipse(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.1 + rng() * 0.12), r * 0.05, rng() * Math.PI, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.45)';
        ctx.fill();
      }
      break;
    }
    case 'OCEANIC': {
      const oHue = 214 + ((hue % 24) - 12) * 0.5;
      const oLight = 36 + (rng() - 0.5) * 6;
      ctx.fillStyle = `hsl(${oHue}, 70%, ${oLight}%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Sparse island chains
      const islands = 2 + Math.floor(rng() * 3);
      for (let i = 0; i < islands; i++) {
        const a = rng() * Math.PI * 2;
        const d = rng() * r * 0.7;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.05 + rng() * 0.07), 0, Math.PI * 2);
        ctx.fillStyle = 'hsla(42, 45%, 55%, 0.85)';
        ctx.fill();
      }
      const clouds = 4 + Math.floor(rng() * 5);
      for (let i = 0; i < clouds; i++) {
        const a = rng() * Math.PI * 2;
        const d = rng() * r * 0.85;
        ctx.beginPath();
        ctx.ellipse(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.1 + rng() * 0.12), r * 0.05, rng() * Math.PI, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
        ctx.fill();
      }
      break;
    }
  }

  ctx.restore(); // end axial-spin transform — terminator + rim light stay star-fixed

  // --- Day/night terminator: dark gradient on the side away from the star ---
  const angToStar = Math.atan2(starY - y, starX - x);
  const lx = x + Math.cos(angToStar) * r;
  const ly = y + Math.sin(angToStar) * r;
  const dxx = x - Math.cos(angToStar) * r;
  const dyy = y - Math.sin(angToStar) * r;
  const term = ctx.createLinearGradient(lx, ly, dxx, dyy);
  term.addColorStop(0, 'rgba(0, 0, 18, 0)');
  term.addColorStop(0.55, 'rgba(0, 0, 18, 0.12)');
  term.addColorStop(1, 'rgba(0, 0, 18, 0.72)');
  ctx.fillStyle = term;
  ctx.fillRect(x - r, y - r, r * 2, r * 2);

  ctx.restore();

  // --- 1px rim light toward the star ---
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0.5, r - 0.5), angToStar - 1.05, angToStar + 1.05);
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
  ctx.lineWidth = 1;
  ctx.stroke();
}

/** Genesis terraforming overlay for a still-forming planet: an accretion halo,
 *  dust motes spiralling inward, and a pulsing dashed containment ring + label.
 *  All motion derives from the shared `t` (seconds) like every other treatment. */
function drawFormingEffect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  hue: number,
  t: number,
  seed: number,
): void {
  const rng = splitmix32((seed ^ 0x6e617363) >>> 0);
  const pulseA = 0.5 + 0.5 * Math.sin(t * 2.1);
  const pulseB = 0.5 + 0.5 * Math.sin(t * 1.7 + 1.2);

  ctx.save();
  // Accretion halo (additive glow around the nascent world).
  ctx.globalCompositeOperation = 'lighter';
  const halo = ctx.createRadialGradient(x, y, r * 0.8, x, y, r * 1.9);
  halo.addColorStop(0, `hsla(${hue}, 80%, 70%, ${0.18 * pulseA})`);
  halo.addColorStop(0.5, `hsla(${hue + 30}, 60%, 55%, ${0.10 * pulseB})`);
  halo.addColorStop(1, `hsla(${hue}, 70%, 50%, 0)`);
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(x, y, r * 1.9, 0, Math.PI * 2);
  ctx.fill();

  // Dust motes spiralling inward (each on its own infall phase).
  const PARTICLES = 22;
  for (let i = 0; i < PARTICLES; i++) {
    const base = rng() * Math.PI * 2;
    const speed = 0.3 + rng() * 0.5;
    const phase = (t * 0.09 + rng()) % 1;
    const ang = base + t * speed;
    const dist = r * (2.0 - 0.95 * phase);
    const px = x + Math.cos(ang) * dist;
    const py = y + Math.sin(ang) * dist * SQUASH;
    ctx.globalAlpha = phase * 0.85;
    ctx.fillStyle = `hsl(${hue + rng() * 40 - 20}, 90%, 75%)`;
    ctx.beginPath();
    ctx.arc(px, py, 0.8 + rng() * 1.2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  // Pulsing dashed containment ring.
  ctx.save();
  ctx.globalAlpha = 0.55 + 0.45 * pulseA;
  ctx.strokeStyle = `hsl(${hue}, 85%, 72%)`;
  ctx.lineWidth = 1.6;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.arc(x, y, r + 4 + pulseB * 3, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();

  // "GENESIS FORMING…" label below the world.
  ctx.save();
  ctx.font = FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const label = 'GENESIS FORMING…';
  const ly = y + r + 6;
  const lw = ctx.measureText(label).width;
  ctx.fillStyle = 'rgba(4, 8, 16, 0.7)';
  ctx.fillRect(x - lw / 2 - 3, ly - 1, lw + 6, 13);
  ctx.globalAlpha = 0.6 + 0.4 * pulseB;
  ctx.fillStyle = `hsl(${hue}, 85%, 72%)`;
  ctx.fillText(label, x, ly);
  ctx.restore();
}

/** Tilted ring ellipse — half=back draws behind the planet, half=front over it. */
function drawRingHalf(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  hue: number,
  tilt: number,
  half: 'back' | 'front'
): void {
  const start = half === 'back' ? Math.PI : 0;
  const end = half === 'back' ? Math.PI * 2 : Math.PI;
  ctx.beginPath();
  ctx.ellipse(x, y, r * 1.9, r * 0.55, tilt, start, end);
  ctx.strokeStyle = `hsla(${hue + 30}, 38%, 70%, 0.5)`;
  ctx.lineWidth = Math.max(1, r * 0.16);
  ctx.stroke();
  ctx.beginPath();
  ctx.ellipse(x, y, r * 1.62, r * 0.46, tilt, start, end);
  ctx.strokeStyle = `hsla(${hue + 30}, 30%, 82%, 0.35)`;
  ctx.lineWidth = Math.max(0.6, r * 0.06);
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// Station glyph — keeps the hex visual language of the legacy viewport
// ---------------------------------------------------------------------------

function drawStationGlyph(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  t: number,
  seedIdx: number
): void {
  ctx.strokeStyle = CYAN;
  ctx.fillStyle = 'rgba(0, 217, 255, 0.3)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i;
    const px = x + size * Math.cos(a);
    const py = y + size * Math.sin(a);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  // Core
  ctx.fillStyle = 'rgba(0, 217, 255, 0.6)';
  ctx.beginPath();
  ctx.arc(x, y, size * 0.4, 0, Math.PI * 2);
  ctx.fill();
  // Blinking status light
  if (Math.sin(t * 3 + seedIdx) > 0.5) {
    ctx.fillStyle = '#00ff41';
    ctx.beginPath();
    ctx.arc(x, y, 1.6, 0, Math.PI * 2);
    ctx.fill();
  }
}

// ---------------------------------------------------------------------------
// Tooltip drawn on-canvas (never DOM)
// ---------------------------------------------------------------------------

function drawTooltip(
  ctx: CanvasRenderingContext2D,
  mx: number,
  my: number,
  lines: string[],
  color: string,
  w: number,
  h: number
): void {
  ctx.font = FONT;
  let tw = 0;
  for (const l of lines) tw = Math.max(tw, ctx.measureText(l).width);
  const pad = 6;
  const lh = 13;
  const bw = tw + pad * 2;
  const bh = lines.length * lh + pad * 2 - 4;
  let bx = mx + 14;
  let by = my + 10;
  if (bx + bw > w - 4) bx = mx - bw - 10;
  if (by + bh > h - 4) by = my - bh - 10;
  ctx.fillStyle = 'rgba(4, 8, 16, 0.88)';
  ctx.fillRect(bx, by, bw, bh);
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.6;
  ctx.lineWidth = 1;
  ctx.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
  ctx.globalAlpha = 1;
  ctx.fillStyle = color;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  lines.forEach((l, i) => ctx.fillText(l, bx + pad, by + pad + i * lh));
}

// ---------------------------------------------------------------------------
// Ship glyph — a small chevron with an engine glow, colored by faction
// ---------------------------------------------------------------------------

function drawShipGlyph(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  color: string,
  angle: number
): void {
  const rgb = hexToRgb(color);
  ctx.save();
  ctx.translate(x, y);
  // Soft engine/contact glow
  const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, size * 2.6);
  glow.addColorStop(0, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.5)`);
  glow.addColorStop(1, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0)`);
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, 0, size * 2.6, 0, Math.PI * 2);
  ctx.fill();
  // Hull chevron pointing along its drift heading
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(size * 1.3, 0);
  ctx.lineTo(-size * 0.9, size * 0.85);
  ctx.lineTo(-size * 0.45, 0);
  ctx.lineTo(-size * 0.9, -size * 0.85);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.55)';
  ctx.lineWidth = 0.7;
  ctx.stroke();
  ctx.restore();
}

/** A ship that has left the sector, mid departure-streak. */
interface ShipDeparture {
  shipId: string;
  x: number;
  y: number;
  angle: number;
  color: string;
  size: number;
  startMs: number;
}

/** A ship warping IN from another sector, mid arrival-streak (it decelerates
 *  from a screen edge to its in-sector position). While active the normal glyph
 *  is suppressed so it doesn't double-draw. */
interface ShipArrival {
  shipId: string;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  angle: number;
  color: string;
  size: number;
  startMs: number;
}

// Deterministic foreground placement for a ship glyph (seeded by ship id) and
// its drifting position/heading at clock t — shared by the live render and the
// departure animation so a ship streaks off from exactly where it last was.
interface ShipPlace {
  baseX: number; baseY: number; phase: number; driftSpd: number; seed: number;
}

function shipPlacement(shipId: string, w: number, h: number): ShipPlace {
  let hseed = 0;
  for (let i = 0; i < shipId.length; i++) hseed = (hseed * 31 + shipId.charCodeAt(i)) >>> 0;
  const srng = splitmix32(hseed || 1);
  return {
    baseX: w * (0.5 + srng() * 0.4),
    baseY: h * (0.16 + srng() * 0.66),
    phase: srng() * Math.PI * 2,
    driftSpd: (0.05 + srng() * 0.07) * SHIP_SCALE,
    seed: hseed || 1,
  };
}

function shipPos(
  p: ShipPlace, w: number, h: number, t: number
): { x: number; y: number; angle: number } {
  const driftAmp = Math.min(w, h) * 0.035;
  const theta = t * p.driftSpd + p.phase;
  const x = p.baseX + Math.cos(theta) * driftAmp;
  const y = p.baseY + Math.sin(theta * 0.8) * driftAmp * 0.6;
  const angle = Math.atan2(Math.cos(theta * 0.8) * 0.48, -Math.sin(theta));
  return { x, y, angle };
}

type ShipMotion = { x: number; y: number; angle: number; docked: boolean };
type DockPoint = { x: number; y: number; kind: string };

// CRUISE→DWELL cycle among a pool of docks: a ship travels to one, parks a
// while, then moves on. Stateless (seed + clock), staggered per ship.
function dockCycle(p: ShipPlace, t: number, pool: DockPoint[]): ShipMotion {
  const period = (22 + (p.seed % 21)) / SHIP_SCALE;
  const cyclePos = t / period + (p.seed % 1000) / 1000;
  const cycle = Math.floor(cyclePos);
  const frac = cyclePos - cycle;
  const pick = (n: number) => pool[(p.seed + n) % pool.length];
  const from = pick(cycle);
  const to = pick(cycle + 1);
  const CRUISE = 0.45;
  if (frac < CRUISE) {
    const e = frac / CRUISE;
    const ease = e * e * (3 - 2 * e); // smoothstep
    return {
      x: from.x + (to.x - from.x) * ease,
      y: from.y + (to.y - from.y) * ease,
      angle: Math.atan2(to.y - from.y, to.x - from.x),
      docked: false,
    };
  }
  const off = 9 + (p.seed % 6);
  const oa = (p.seed % 360) * Math.PI / 180;
  return {
    x: to.x + Math.cos(oa) * off,
    y: to.y + Math.sin(oa) * off,
    angle: Math.atan2(to.y - from.y, to.x - from.x),
    docked: true,
  };
}

// Steady orbit around a body (a colonist/science ship circling a planet it is
// servicing). Tangential heading; foreshortened to match the orbital plane.
function orbitBody(cx: number, cy: number, p: ShipPlace, t: number, radius: number): ShipMotion {
  const spd = (0.25 + (p.seed % 30) / 100) * SHIP_SCALE;
  const a = (p.seed % 360) * Math.PI / 180 + t * spd;
  const x = cx + Math.cos(a) * radius;
  const y = cy + Math.sin(a) * radius * 0.55;
  // Heading tangent to the (squashed) orbit
  const tx = -Math.sin(a);
  const ty = Math.cos(a) * 0.55;
  return { x, y, angle: Math.atan2(ty, tx), docked: false };
}

// Outbound: a commuting ship heading for the sector edge to warp away. Eases
// from its drift spot toward a seeded edge point and loiters near the rim.
function outbound(p: ShipPlace, w: number, h: number, t: number): ShipMotion {
  const base = shipPos(p, w, h, t);
  const ang = (p.seed % 360) * Math.PI / 180;
  const edgeX = w * (0.5 + Math.cos(ang) * 0.52);
  const edgeY = h * (0.5 + Math.sin(ang) * 0.46);
  const e = 0.55 + 0.45 * Math.sin(t * 0.05 * SHIP_SCALE + p.phase); // breathe toward/from rim
  const x = base.x + (edgeX - base.x) * Math.max(0, e);
  const y = base.y + (edgeY - base.y) * Math.max(0, e);
  return { x, y, angle: Math.atan2(edgeY - base.y, edgeX - base.x), docked: false };
}

// Behavior dispatcher: map a ship's real activity/mission/archetype to a
// recognizable on-screen behavior. Purely cosmetic visualization of NPC state
// (intra-sector pixel position is not game state), but legible: orbiting
// planets, docking, rendezvousing, heading out, patrolling, or drifting.
function shipBehavior(
  p: ShipPlace, w: number, h: number, t: number,
  docks: DockPoint[],
  activity: string | null | undefined,
  mission: string | null | undefined,
  archetype: string | null | undefined,
  rendezvous: { x: number; y: number } | null
): ShipMotion {
  const act = (activity || '').toUpperCase();
  const arch = (archetype || '').toUpperCase();
  const planets = docks.filter((d) => d.kind === 'planet');
  const stations = docks.filter((d) => d.kind === 'station');

  // SOCIALIZE → rendezvous: ease to the shared meeting point and sit there.
  if (act === 'SOCIALIZE' && rendezvous) {
    const base = shipPos(p, w, h, t);
    const e = 0.5 + 0.5 * Math.sin(t * 0.06 * SHIP_SCALE + p.phase);
    return {
      x: base.x + (rendezvous.x - base.x) * e,
      y: base.y + (rendezvous.y - base.y) * e,
      angle: Math.atan2(rendezvous.y - base.y, rendezvous.x - base.x),
      docked: e > 0.85,
    };
  }

  // COMMUTE → outbound toward the rim (about to warp out).
  if (act === 'COMMUTE') return outbound(p, w, h, t);

  // PATROL (law/raider) → sweep between dock points across the sector.
  if (act === 'PATROL') {
    const pool = docks.length >= 2 ? docks : [];
    if (pool.length >= 2) return dockCycle(p, t, pool);
    return { ...shipPos(p, w, h, t), docked: false };
  }

  // WORK_STATION → actually servicing a stop here.
  if (act === 'WORK_STATION') {
    const wantPlanet = mission === 'colonist' || mission === 'science';
    if (wantPlanet && planets.length > 0) {
      const target = planets[p.seed % planets.length];
      return orbitBody(target.x, target.y, p, t, 16 + (p.seed % 8));
    }
    const pool = stations.length > 0 ? stations : docks;
    if (pool.length > 0) return dockCycle(p, t, pool);
    return { ...shipPos(p, w, h, t), docked: true };
  }

  // Raiders with no explicit activity prowl between points; everyone else
  // (SLEEP / unknown) drifts gently.
  if (arch === 'HOSTILE_RAIDER' && docks.length >= 2) return dockCycle(p, t, docks);
  return { ...shipPos(p, w, h, t), docked: false };
}

// ---------------------------------------------------------------------------
// Orbital closeup — a single planet filling the viewport, "from orbit"
// ---------------------------------------------------------------------------

function drawOrbitCloseup(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sectorId: number,
  body: SystemBody,
  t: number,
  progress: number,
  fromX: number,
  fromY: number,
  fromR: number
): void {
  // Deep space + drifting starfield (parallax conveys the zoom settling in)
  ctx.fillStyle = '#040711';
  ctx.fillRect(0, 0, w, h);
  const rng = splitmix32(sectorId * 7 + 11);
  ctx.fillStyle = '#ffffff';
  for (let i = 0; i < 150; i++) {
    const x0 = rng() * w;
    const y0 = rng() * h;
    const size = 0.3 + rng() * 1.4;
    const bright = (0.3 + rng() * 0.7) * 0.7;
    const x = (((x0 - t * 2.0) % w) + w) % w;
    ctx.globalAlpha = bright;
    ctx.fillRect(x, y0, size, size);
  }
  ctx.globalAlpha = 1;

  const targetCx = w * 0.44;
  const targetCy = h * 0.54;
  const bigR = Math.min(w * 0.30, h * 0.42);
  const ease = 1 - Math.pow(1 - Math.max(0, Math.min(1, progress)), 3);
  // Camera push-in: interpolate the planet from its clicked position/size in
  // the system view to the centered closeup, so it visibly zooms IN on the
  // body the player picked rather than snapping to a centered view.
  const cx = fromX + (targetCx - fromX) * ease;
  const cy = fromY + (targetCy - fromY) * ease;
  const r = Math.max(4, fromR + (bigR - fromR) * ease);
  const seed = (sectorId * 101 + body.slot * 7919 + Math.round(body.palette.hue)) >>> 0;
  // Light from far off-screen left → a crescent terminator (true orbital look)
  const lightX = -w * 0.5;
  const lightY = cy - h * 0.12;
  const ringTilt = -0.32 + ((seed % 100) / 100 - 0.5) * 0.3;

  // Atmosphere halo behind the limb
  const atm = ctx.createRadialGradient(cx, cy, r * 0.92, cx, cy, r * 1.4);
  atm.addColorStop(0, `hsla(${body.palette.hue}, 70%, 62%, 0.28)`);
  atm.addColorStop(1, `hsla(${body.palette.hue}, 70%, 62%, 0)`);
  ctx.fillStyle = atm;
  ctx.beginPath();
  ctx.arc(cx, cy, r * 1.4, 0, Math.PI * 2);
  ctx.fill();

  if (body.rings) drawRingHalf(ctx, cx, cy, r, body.palette.hue, ringTilt, 'back');
  drawPlanetSurface(ctx, body, cx, cy, r, lightX, lightY, seed, t);
  if (body.rings) drawRingHalf(ctx, cx, cy, r, body.palette.hue, ringTilt, 'front');

  // A couple of moons sweeping the closeup for scale + motion — all on the
  // planet's single shared orbital plane (coplanar), far side dimmed.
  const moonRng = splitmix32(seed + 9);
  const moonCount = Math.min(3, body.moons);
  const moonTilt = ((seed % 200) / 200 - 0.5) * 1.0;
  for (let m = 0; m < moonCount; m++) {
    const mo = moonRng() * Math.PI * 2;
    const ms = (0.25 + moonRng() * 0.3) * MOTION_SCALE;
    const mr = r * (1.5 + m * 0.4);
    const ma = mo + t * ms;
    const mp = moonPlanePos(cx, cy, mr, ma, moonTilt, 0.42);
    ctx.beginPath();
    ctx.arc(mp.x, mp.y, 2.4, 0, Math.PI * 2);
    ctx.fillStyle = mp.depth >= 0 ? 'rgba(215, 215, 230, 0.9)' : 'rgba(150, 150, 170, 0.6)';
    ctx.fill();
  }
}

// ---------------------------------------------------------------------------
// Scene draw — pure function of (snapshot, size, clock)
// ---------------------------------------------------------------------------

function drawScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sectorId: number,
  system: SystemSnapshot | null,
  t: number,
  hitTargets: HitTarget[],
  hover: { target: HitTarget; mx: number; my: number } | null,
  hazardLevel: number,
  radiationLevel: number,
  ships: ShipPresence[] = [],
  departures: ShipDeparture[] = [],
  selectedShipId: string | null = null,
  arrivals: ShipArrival[] = []
): void {
  hitTargets.length = 0;

  // 1) Deep space background
  ctx.fillStyle = '#040711';
  ctx.fillRect(0, 0, w, h);

  // 2) Parallax starfield — two layers, deterministic from sector_id
  const layers = [
    { count: 110, speed: 1.6, sizeMax: 1.1, alpha: 0.5, seed: sectorId * 7 + 11 },
    { count: 60, speed: 4.2, sizeMax: 1.8, alpha: 0.85, seed: sectorId * 13 + 29 }
  ];
  for (const layer of layers) {
    const rng = splitmix32(layer.seed);
    ctx.fillStyle = '#ffffff';
    for (let i = 0; i < layer.count; i++) {
      const x0 = rng() * w;
      const y0 = rng() * h;
      const size = 0.3 + rng() * layer.sizeMax;
      const bright = (0.3 + rng() * 0.7) * layer.alpha;
      const x = (((x0 - t * layer.speed) % w) + w) % w;
      ctx.globalAlpha = bright;
      ctx.beginPath();
      ctx.arc(x, y0, size, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;

  if (!system) {
    // Loading — starfield only, plus a dim CRT scan-acquisition line.
    ctx.save();
    ctx.font = FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(0, 217, 255, 0.32)';
    ctx.fillText('ACQUIRING SYSTEM SCAN…', w / 2, h / 2);
    ctx.restore();
    return;
  }

  // 3) Nebula haze
  if (system.nebula) {
    const rng = splitmix32(sectorId * 31 + 777);
    const blobCount = 5 + Math.floor(rng() * 3);
    const alpha = Math.min(0.2, Math.max(0.03, system.nebula.density * 0.12));
    for (let i = 0; i < blobCount; i++) {
      const cx = rng() * w + Math.sin(t * 0.01 + i) * 9;
      const cy = rng() * h + Math.cos(t * 0.008 + i * 2) * 6;
      const rad = (0.22 + rng() * 0.45) * Math.max(w, h);
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      grad.addColorStop(0, `hsla(${system.nebula.hue}, 70%, 55%, ${alpha})`);
      grad.addColorStop(1, `hsla(${system.nebula.hue}, 70%, 55%, 0)`);
      ctx.fillStyle = grad;
      ctx.fillRect(cx - rad, cy - rad, rad * 2, rad * 2);
    }
  }

  // Scene geometry — star left-of-center, orbits squashed ellipses
  // Seeded star-anchor jitter (±3% w, ±4% h) so the layout skeleton varies
  // per sector instead of every system sharing one fixed anchor.
  const anchorRng = splitmix32(sectorId * 2654435761 + 97);
  // Star anchored just right of centre (Max: "move the sun right so we can see
  // more of the rotation") — centring the primary lets the FULL orbital ellipse
  // fall on-screen instead of the left arc clipping the cap. Small seeded
  // jitter keeps systems from sharing one fixed skeleton.
  const starX = w * 0.54 + (anchorRng() - 0.5) * 2 * (w * 0.03);
  const starY = h * 0.52 + (anchorRng() - 0.5) * 2 * (h * 0.04);
  const margin = 14;
  // Cap orbital extent so the outermost ellipse stays fully on-screen on BOTH
  // sides of the (now more central) star — left reach (starX - margin), right
  // reach (w - starX - margin), and the vertical squash bound.
  const rxMax = Math.min(
    (h * 0.5 - margin) / SQUASH,
    starX - margin,
    w - starX - margin
  );
  const bodyScale = Math.min(2.2, Math.max(0.8, Math.min(w, h) / 340));

  // Extra stars (STAR_CLUSTER) scattered behind everything else
  if (system.extra_stars && system.extra_stars.length > 0) {
    const rng = splitmix32(sectorId * 17 + 5);
    system.extra_stars.forEach((es) => {
      const ex = w * (0.08 + rng() * 0.84);
      const ey = h * (0.1 + rng() * 0.8);
      const er = Math.min(w, h) * (0.012 + rng() * 0.02);
      drawStar(ctx, es.kind, es.color, ex, ey, er, w, h);
    });
  }

  // 4) Orbit arcs (faint ellipses with perspective squash)
  const orbitAus = new Set<number>();
  system.bodies.forEach((b) => orbitAus.add(b.orbit_au));
  system.stations.forEach((s) => orbitAus.add(s.orbit_au));
  ctx.strokeStyle = 'rgba(120, 140, 200, 0.12)';
  ctx.lineWidth = 1;
  orbitAus.forEach((au) => {
    const rx = au * rxMax;
    ctx.beginPath();
    ctx.ellipse(starX, starY, rx, rx * SQUASH, 0, 0, Math.PI * 2);
    ctx.stroke();
  });

  // 4b) Habitable-zone band — a soft green annulus where liquid-water worlds
  // live (habitable planets are placed inside it). Drawn behind the bodies.
  if (system.habitable_zone) {
    const hz = system.habitable_zone;
    const rIn = hz.inner_au * rxMax;
    const rOut = hz.outer_au * rxMax;
    ctx.save();
    // Filled ring via even-odd: outer ellipse minus inner ellipse.
    ctx.beginPath();
    ctx.ellipse(starX, starY, rOut, rOut * SQUASH, 0, 0, Math.PI * 2);
    ctx.ellipse(starX, starY, rIn, rIn * SQUASH, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(40, 200, 90, 0.07)';
    ctx.fill('evenodd');
    // Edge rings to delineate the band
    ctx.strokeStyle = 'rgba(60, 220, 110, 0.28)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.ellipse(starX, starY, rIn, rIn * SQUASH, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(starX, starY, rOut, rOut * SQUASH, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    // Label on the band's right edge
    ctx.font = FONT;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(90, 230, 130, 0.7)';
    ctx.fillText('HABITABLE ZONE', starX + rIn + 4, starY - (rIn + rOut) / 2 * SQUASH - 4);
    ctx.restore();
  }

  // 6) Asteroid belt — speckled annulus, two passes for depth.
  //    speckleRing renders a seeded particle annulus (shared by the grey
  //    asteroid belt and the reddish collision-debris ring).
  const speckleRing = (
    inner: number, outer: number, pass: 'back' | 'front',
    seed: number, count: number, color: string, chunks: number
  ) => {
    const rng = splitmix32(seed);
    for (let i = 0; i < count; i++) {
      const frac = inner + rng() * Math.max(0.01, outer - inner);
      const a0 = rng() * Math.PI * 2;
      const speed = MOTION_SCALE * (0.018 + rng() * 0.014) / Math.max(0.1, frac);
      const size = 0.5 + rng() * 1.1;
      const alpha = 0.2 + rng() * 0.4;
      const ang = a0 + t * speed;
      const ax = starX + Math.cos(ang) * frac * rxMax;
      const ay = starY + Math.sin(ang) * frac * rxMax * SQUASH;
      const isBack = ay < starY;
      if ((pass === 'back') !== isBack) continue;
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.fillRect(ax, ay, size, size);
    }
    // A few larger shattered chunks (collision-ring flavor)
    for (let c = 0; c < chunks; c++) {
      const frac = inner + rng() * Math.max(0.01, outer - inner);
      const a0 = rng() * Math.PI * 2;
      const speed = MOTION_SCALE * 0.014 / Math.max(0.1, frac);
      const ang = a0 + t * speed;
      const ax = starX + Math.cos(ang) * frac * rxMax;
      const ay = starY + Math.sin(ang) * frac * rxMax * SQUASH;
      const isBack = ay < starY;
      if ((pass === 'back') !== isBack) continue;
      ctx.globalAlpha = 0.7;
      ctx.fillStyle = color;
      ctx.fillRect(ax - 1, ay - 1, 2.4 + rng() * 1.6, 2.0 + rng() * 1.4);
    }
    ctx.globalAlpha = 1;
  };
  const drawBelt = (pass: 'back' | 'front') => {
    if (system.belt) speckleRing(system.belt.inner_au, system.belt.outer_au, pass, sectorId * 41 + 1337, 110, '#aaaabe', 0);
  };
  // Collision-debris ring — reddish, denser, with shattered chunks.
  const drawDebrisRing = (pass: 'back' | 'front') => {
    if (!system.debris) return;
    const hue = system.debris.hue;
    speckleRing(system.debris.inner_au, system.debris.outer_au, pass, sectorId * 9176 + 4242, 150, `hsl(${hue}, 30%, 45%)`, 7);
  };
  drawBelt('back');
  drawDebrisRing('back');

  // 3/5) Star + bodies + stations, depth-sorted by screen y
  const drawables: Array<{ y: number; draw: () => void }> = [];
  // Dock targets ships travel to and dwell at (stations + planets), captured at
  // their CURRENT screen positions this frame so ships home on moving bodies.
  const dockPoints: Array<{ x: number; y: number; kind: string }> = [];

  if (system.star) {
    const star = system.star;
    const sr = starRadius(star.kind, w, h);
    hitTargets.push({
      x: starX, y: starY, r: sr, kind: 'star',
      name: star.label || star.kind,
      lines: [(star.label || 'PRIMARY STAR').toUpperCase()],
      meta: {
        kind: 'star',
        label: star.label || 'PRIMARY STAR',
        starClass: (star.kind || 'UNKNOWN').replace(/_/g, ' '),
        color: star.color
      }
    });
    drawables.push({
      y: starY,
      draw: () => {
        drawStar(ctx, star.kind, star.color, starX, starY, sr, w, h);
        if (star.secondary) {
          drawStar(
            ctx, star.secondary.kind, star.secondary.color,
            starX + sr * 1.9, starY - sr * 0.85, sr * 0.5, w, h
          );
        }
      }
    });
  }

  // Bodies on their orbits with slow deterministic drift
  system.bodies.forEach((body, bodyIdx) => {
    const rx = body.orbit_au * rxMax;
    const ry = rx * SQUASH;
    // Angular speed ~ 1/orbit_au — full orbit takes minutes (slowed by the
    // global pacing knob so the rotation reads calm and watchable).
    const omega = ORBIT_SCALE * (Math.PI * 2) / (180 + body.orbit_au * 420);
    const ang = (body.phase_deg * Math.PI) / 180 + t * omega;
    const x = starX + Math.cos(ang) * rx;
    const y = starY + Math.sin(ang) * ry;
    let r = (3 + body.size_class * 2.1) * bodyScale;
    if (body.real) r *= 1.2;
    const seed = (sectorId * 101 + body.slot * 7919 + Math.round(body.palette.hue)) >>> 0;
    dockPoints.push({ x, y, kind: 'planet' }); // colonist/science ships land here

    // Hit target (real planets are click targets; procedural get flavor hover)
    if (body.real && body.planet_id) {
      const hab = typeof body.habitability === 'number' ? ` — HAB ${Math.round(body.habitability)}%` : '';
      hitTargets.push({
        x, y, r: r + 6, kind: 'planet', id: body.planet_id,
        name: body.name || 'UNKNOWN',
        lines: [
          (body.name || 'UNKNOWN').toUpperCase(),
          body.formation_status === 'forming'
            ? 'GENESIS TERRAFORMING IN PROGRESS'
            : `${body.kind.replace(/_/g, ' ').toUpperCase()}${hab}${body.owned ? ' — CLAIMED' : ''}`
        ],
        meta: {
          kind: 'planet',
          planetId: body.planet_id,
          planetKind: body.kind,
          habitability: body.habitability,
          owned: body.owned
        }
      });
    } else {
      hitTargets.push({
        x, y, r: r + 4, kind: 'procedural',
        name: `slot-${body.slot}`,
        lines: [flavorFor(body.kind)],
        meta: {
          kind: 'procedural',
          designation: proceduralDesignation(sectorId, bodyIdx),
          typeName: typeNameFor(body.kind),
          sizeDesc: sizeDescriptorFor(body.size_class)
        }
      });
    }

    drawables.push({
      y,
      draw: () => {
        const forming = body.formation_status === 'forming';
        const ringTilt = -0.32 + ((seed % 100) / 100 - 0.5) * 0.3;
        if (body.rings) drawRingHalf(ctx, x, y, r, body.palette.hue, ringTilt, 'back');
        if (forming) {
          // The nascent world shows through faintly while it coalesces.
          ctx.save();
          ctx.globalAlpha = 0.35 + 0.15 * Math.sin(t * 1.2);
          drawPlanetSurface(ctx, body, x, y, r, starX, starY, seed, t);
          ctx.restore();
          drawFormingEffect(ctx, x, y, r, body.palette.hue, t, seed);
        } else {
          drawPlanetSurface(ctx, body, x, y, r, starX, starY, seed, t);
        }
        if (body.rings) drawRingHalf(ctx, x, y, r, body.palette.hue, ringTilt, 'front');

        // Moons — tiny dots, all on this planet's SINGLE shared orbital plane
        // (coplanar); far-side moons dimmed for a 3-D read.
        const moonRng = splitmix32(seed + 9);
        const moonTilt = ((seed % 200) / 200 - 0.5) * 1.0;
        for (let m = 0; m < body.moons; m++) {
          const mo = moonRng() * Math.PI * 2;
          const ms = (0.4 + moonRng() * 0.5) * MOTION_SCALE;
          const mr = r + 3 + m * 3.2;
          const ma = mo + t * ms;
          const mp = moonPlanePos(x, y, mr, ma, moonTilt, 0.34);
          ctx.beginPath();
          ctx.arc(mp.x, mp.y, 1.1, 0, Math.PI * 2);
          ctx.fillStyle = mp.depth >= 0 ? 'rgba(205, 205, 220, 0.85)' : 'rgba(140, 140, 160, 0.55)';
          ctx.fill();
        }

        if (body.real) {
          // Name label beneath real planets — cyan, mono, uppercase
          ctx.font = FONT;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          const label = (body.name || 'UNKNOWN').toUpperCase();
          const ly = y + r + 6;
          ctx.fillStyle = 'rgba(4, 8, 16, 0.7)';
          const lw2 = ctx.measureText(label).width;
          ctx.fillRect(x - lw2 / 2 - 3, ly - 1, lw2 + 6, 13);
          ctx.fillStyle = CYAN;
          ctx.fillText(label, x, ly);
        }
      }
    });
  });

  // Stations on stable orbits
  system.stations.forEach((st, idx) => {
    const rx = st.orbit_au * rxMax;
    const ry = rx * SQUASH;
    const omega = ORBIT_SCALE * (Math.PI * 2) / (160 + st.orbit_au * 380);
    const ang = (st.phase_deg * Math.PI) / 180 + t * omega;
    const x = starX + Math.cos(ang) * rx;
    const y = starY + Math.sin(ang) * ry;
    const size = 6.5 * Math.min(1.4, bodyScale);
    dockPoints.push({ x, y, kind: 'station' }); // commerce ships dock here

    hitTargets.push({
      x, y, r: size + 7, kind: 'station', id: st.station_id,
      name: st.name,
      lines: [st.name.toUpperCase(), (st.type || 'STATION').replace(/_/g, ' ').toUpperCase()],
      meta: { kind: 'station', stationId: st.station_id, stationType: st.type || 'STATION' }
    });

    drawables.push({
      y,
      draw: () => drawStationGlyph(ctx, x, y, size, t, idx)
    });
  });

  drawables.sort((a, b) => a.y - b.y);
  drawables.forEach((d) => d.draw());

  drawBelt('front');
  drawDebrisRing('front');

  // Ships in the sector — foreground contacts (NPC captains, other pilots),
  // each animated by its real activity/mission/archetype (orbit a planet, dock
  // at a station, rendezvous in empty space, head out to warp, patrol, drift).
  // Each is a click target → contact popup.
  //
  // Rendezvous pairing: socializing ships pair off (sorted by id, consecutive)
  // and each pair shares a seeded meeting point in empty space, away from any
  // station/planet, so two contacts visibly meet up.
  const rendezvousById = new Map<string, { x: number; y: number }>();
  const socialIds = ships
    .filter((s) => s && s.ship_id && (s.activity || '').toUpperCase() === 'SOCIALIZE')
    .map((s) => s.ship_id as string)
    .sort();
  for (let i = 0; i + 1 < socialIds.length; i += 2) {
    const a = socialIds[i], b = socialIds[i + 1];
    let hs = 0; const key = a + b;
    for (let k = 0; k < key.length; k++) hs = (hs * 31 + key.charCodeAt(k)) >>> 0;
    const rr = splitmix32(hs || 1);
    const pt = { x: w * (0.32 + rr() * 0.4), y: h * (0.28 + rr() * 0.5) };
    rendezvousById.set(a, pt);
    rendezvousById.set(b, pt);
  }

  // Ships currently warping IN have their normal glyph suppressed (the arrival
  // streak draws them) until the arrival completes.
  const arrivingNow = new Set<string>();
  const arrNow = Date.now();
  for (const a of arrivals) { if (arrNow - a.startMs < 1100) arrivingNow.add(a.shipId); }

  ships.forEach((s) => {
    if (!s || !s.ship_id) return;
    if (arrivingNow.has(s.ship_id)) return; // arrival animation owns it this frame
    const place = shipPlacement(s.ship_id, w, h);
    const { x, y, angle, docked } = shipBehavior(
      place, w, h, t, dockPoints, s.activity, s.mission, s.archetype,
      rendezvousById.get(s.ship_id) || null
    );
    const size = (docked ? 4.6 : 6.0) * Math.min(1.5, bodyScale);
    const fac = shipFaction(s);
    const contactName = (s.ship_name || s.username || 'CONTACT').toUpperCase();
    const captain = s.username || (s.is_npc ? 'NPC' : 'PILOT');
    hitTargets.push({
      x, y, r: size + 8, kind: 'ship', id: s.ship_id,
      name: contactName,
      lines: [contactName, `${fac.label}${s.ship_type ? ' — ' + s.ship_type.replace(/_/g, ' ') : ''}`],
      meta: {
        kind: 'ship', shipId: s.ship_id, shipName: s.ship_name || contactName,
        shipType: s.ship_type || 'UNKNOWN', captain, isNpc: !!s.is_npc,
        factionLabel: fac.label, factionColor: fac.color, lawful: fac.lawful,
        notoriety: typeof s.notoriety === 'number' ? s.notoriety : undefined
      }
    });
    if (docked) {
      // Parked at a dock — dimmed so cruising ships read as the active ones.
      ctx.save();
      ctx.globalAlpha = 0.7;
      drawShipGlyph(ctx, x, y, size, fac.color, angle);
      ctx.restore();
    } else {
      drawShipGlyph(ctx, x, y, size, fac.color, angle);
    }
  });

  // Selection reticle — the COMMS-selected contact gets a bold pulsing ring +
  // corner ticks so picking a contact spotlights its ship in the windshield.
  // Re-anchored to the glyph's CURRENT position each frame (like hover).
  if (selectedShipId) {
    const sel = hitTargets.find((ht) => ht.kind === 'ship' && ht.id === selectedShipId);
    if (sel) {
      const pulse = 0.5 + 0.5 * Math.sin(t * 4);
      const rr = Math.max(15, sel.r) + 6 + pulse * 4;
      ctx.save();
      // Soft green halo so the spotlight reads instantly against the dark field
      const halo = ctx.createRadialGradient(sel.x, sel.y, rr * 0.4, sel.x, sel.y, rr + 8);
      halo.addColorStop(0, 'rgba(0, 255, 65, 0)');
      halo.addColorStop(0.7, `rgba(0, 255, 65, ${0.12 + 0.1 * pulse})`);
      halo.addColorStop(1, 'rgba(0, 255, 65, 0)');
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(sel.x, sel.y, rr + 8, 0, Math.PI * 2);
      ctx.fill();
      // Bold pulsing selection ring
      ctx.strokeStyle = `rgba(40, 255, 90, ${0.85 + 0.15 * pulse})`;
      ctx.lineWidth = 2.6;
      ctx.beginPath();
      ctx.arc(sel.x, sel.y, rr, 0, Math.PI * 2);
      ctx.stroke();
      // Four corner ticks framing the contact
      ctx.lineWidth = 2.2;
      for (let q = 0; q < 4; q++) {
        const a = Math.PI / 4 + (Math.PI / 2) * q;
        const ix = sel.x + Math.cos(a) * rr;
        const iy = sel.y + Math.sin(a) * rr;
        const ox = sel.x + Math.cos(a) * (rr + 7);
        const oy = sel.y + Math.sin(a) * (rr + 7);
        ctx.beginPath();
        ctx.moveTo(ix, iy);
        ctx.lineTo(ox, oy);
        ctx.stroke();
      }
      ctx.font = 'bold 11px "Courier New", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = 'rgba(120, 255, 150, 1)';
      ctx.fillText('◉ SELECTED', sel.x, sel.y - rr - 7);
      ctx.restore();
    }
  }

  // Departing ships — a ship that left the sector streaks off into the
  // distance (accelerating along its heading, shrinking + fading, with a warp
  // trail) and is then pruned from the list.
  if (departures.length) {
    const DEP_MS = 1300;
    const nowMs = Date.now();
    const reach = Math.max(w, h) * 1.5;
    for (let i = departures.length - 1; i >= 0; i--) {
      const d = departures[i];
      const p = (nowMs - d.startMs) / DEP_MS;
      if (p >= 1) { departures.splice(i, 1); continue; }
      if (p < 0) continue; // staggered launch time not reached yet
      const travel = p * p * reach; // ease-in: accelerate away
      const dx = d.x + Math.cos(d.angle) * travel;
      const dy = d.y + Math.sin(d.angle) * travel;
      const sz = Math.max(0.5, d.size * (1 - 0.8 * p));
      const alpha = 1 - p;
      // Warp trail behind the hull
      ctx.save();
      ctx.globalAlpha = alpha * 0.55;
      ctx.strokeStyle = d.color;
      ctx.lineWidth = Math.max(0.6, sz * 0.6);
      ctx.beginPath();
      ctx.moveTo(dx - Math.cos(d.angle) * sz * 7, dy - Math.sin(d.angle) * sz * 7);
      ctx.lineTo(dx, dy);
      ctx.stroke();
      ctx.restore();
      ctx.save();
      ctx.globalAlpha = alpha;
      drawShipGlyph(ctx, dx, dy, sz, d.color, d.angle);
      ctx.restore();
    }
  }

  // Arriving ships — a ship warping IN from another sector decelerates from a
  // screen edge to its in-sector spot (ease-out), growing + fading in with a
  // warp trail, then hands off to the normal glyph.
  if (arrivals.length) {
    const ARR_MS = 1100;
    const nowMs = Date.now();
    for (let i = arrivals.length - 1; i >= 0; i--) {
      const a = arrivals[i];
      const p = (nowMs - a.startMs) / ARR_MS;
      if (p >= 1) { arrivals.splice(i, 1); continue; }
      if (p < 0) continue; // staggered entry not reached yet
      const ease = 1 - Math.pow(1 - p, 3); // ease-out: rush in, settle
      const ax = a.fromX + (a.toX - a.fromX) * ease;
      const ay = a.fromY + (a.toY - a.fromY) * ease;
      const sz = Math.max(0.5, a.size * (0.3 + 0.7 * ease));
      const alpha = Math.min(1, p * 2);
      ctx.save();
      ctx.globalAlpha = alpha * 0.5;
      ctx.strokeStyle = a.color;
      ctx.lineWidth = Math.max(0.6, sz * 0.6);
      ctx.beginPath();
      ctx.moveTo(ax - Math.cos(a.angle) * sz * 9 * (1 - ease), ay - Math.sin(a.angle) * sz * 9 * (1 - ease));
      ctx.lineTo(ax, ay);
      ctx.stroke();
      ctx.restore();
      ctx.save();
      ctx.globalAlpha = alpha;
      drawShipGlyph(ctx, ax, ay, sz, a.color, a.angle);
      ctx.restore();
    }
  }

  // Hover affordance: faint reticle ring around the hovered hittable body,
  // re-anchored to its CURRENT orbital position each frame (one extra stroke,
  // no state churn — hover lives in a ref).
  if (hover) {
    const cur = hitTargets.find((ht) =>
      ht.kind === hover.target.kind &&
      (hover.target.id ? ht.id === hover.target.id : ht.name === hover.target.name)
    );
    if (cur) {
      ctx.beginPath();
      ctx.arc(cur.x, cur.y, Math.max(12, cur.r) + 3, 0, Math.PI * 2);
      ctx.strokeStyle = cur.kind === 'procedural'
        ? 'rgba(158, 150, 184, 0.35)'
        : 'rgba(0, 217, 255, 0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // Environmental overlays — parity with the legacy viewscreen
  if (radiationLevel > 0) {
    ctx.fillStyle = `rgba(0, 255, 65, ${Math.min(0.5, radiationLevel) * 0.12})`;
    ctx.fillRect(0, 0, w, h);
  }
  if (hazardLevel > 5) {
    const pulse = Math.sin(t * 5) * 0.5 + 0.5;
    ctx.strokeStyle = `rgba(255, 107, 0, ${pulse * 0.3})`;
    ctx.lineWidth = 3;
    ctx.strokeRect(2, 2, w - 4, h - 4);
  }

  // Hover tooltip — drawn last, on top of everything
  if (hover) {
    const color = hover.target.kind === 'procedural' ? PROC_GREY : CYAN;
    drawTooltip(ctx, hover.mx, hover.my, hover.target.lines, color, w, h);
  }
}

// ---------------------------------------------------------------------------
// LANDED scene — planet-surface vista (horizon gradient, haze, parallax ridges)
// ---------------------------------------------------------------------------

interface LandedPalette {
  skyTop: string;
  skyMid: string;
  horizon: string;
  /** rgba() string for the low atmospheric glow near the horizon */
  glow: string;
  /** "r, g, b" triplet for the drifting haze bands */
  haze: string;
  /** ridge silhouettes, back → front (front is darkest) */
  ridges: [string, string, string];
  /** per-type surface signature flourish (drives drawLandedScene flourishes) */
  flourish: 'VOLCANIC' | 'ICE' | 'OCEANIC' | 'DESERT' | 'TERRAN' | 'MOUNTAINOUS' | 'NONE';
  /** "r, g, b" tint for habitability flora tufts (green for living worlds) */
  flora: string;
}

/** Sky/ridge palette per planet type — reuses the treatment mapping above. */
function landedPalette(planetType?: string): LandedPalette {
  // treatmentFor() buckets every UNKNOWN kind into BARREN (gray). But the
  // cockpit tint class (getPlanetTintClass → base [class*='planet-tint-'])
  // paints unknown types violet-dusk. Align the two: only paint the barren
  // gray sky for a GENUINELY barren kind; anything unrecognized falls through
  // to the violet-dusk default below (matches the legacy landed-band gradient
  // and the tint accent), so a landed scene and its planet card agree.
  const kind = (planetType || '').toUpperCase().replace('PLANETTYPE.', '');
  // MOUNTAINOUS gets a dedicated stone-grey identity (distinct from dead BARREN)
  // WITHOUT disturbing treatmentFor() — which still buckets it to BARREN for the
  // flight-scene popup/painter, keeping that lane identical.
  if (kind === 'MOUNTAINOUS') {
    return {
      skyTop: '#0c0e12', skyMid: '#2a2d36', horizon: '#6b6f7e',
      glow: 'rgba(200, 205, 220, 0.32)', haze: '170, 175, 190',
      ridges: ['#4a4e5b', '#33363f', '#191b22'],
      flourish: 'MOUNTAINOUS', flora: '120, 150, 110'
    };
  }
  const treatment = treatmentFor(planetType || '');
  const effective = treatment === 'BARREN' && !KNOWN_BARREN.has(kind)
    ? 'GAS_GIANT' // violet-dusk default branch
    : treatment;
  switch (effective) {
    case 'VOLCANIC':
      return {
        skyTop: '#120305', skyMid: '#3a0d08', horizon: '#8a2e0a',
        glow: 'rgba(255, 110, 30, 0.5)', haze: '255, 90, 20',
        ridges: ['#2a0c08', '#1a0705', '#0c0303'],
        flourish: 'VOLCANIC', flora: '90, 120, 70'
      };
    case 'ICE':
      return {
        skyTop: '#0c1622', skyMid: '#27435c', horizon: '#9cc4dd',
        glow: 'rgba(210, 235, 255, 0.45)', haze: '190, 220, 240',
        ridges: ['#5d7c93', '#3b566c', '#1d2f40'],
        flourish: 'ICE', flora: '150, 190, 170'
      };
    case 'TERRAN':
      return {
        skyTop: '#04121f', skyMid: '#0d3a4a', horizon: '#2f8c74',
        glow: 'rgba(150, 230, 200, 0.4)', haze: '120, 210, 180',
        ridges: ['#14463c', '#0d2f29', '#061a16'],
        flourish: 'TERRAN', flora: '90, 210, 130'
      };
    case 'OCEANIC':
      return {
        skyTop: '#03101f', skyMid: '#0a3550', horizon: '#2a7f9e',
        glow: 'rgba(120, 210, 235, 0.4)', haze: '110, 190, 220',
        ridges: ['#0f3f55', '#0a2b3c', '#051824'],
        flourish: 'OCEANIC', flora: '80, 200, 160'
      };
    case 'DESERT':
      return {
        skyTop: '#190b04', skyMid: '#4a2410', horizon: '#c07a2e',
        glow: 'rgba(255, 190, 90, 0.45)', haze: '230, 160, 70',
        ridges: ['#5c3014', '#3c1f0c', '#201006'],
        flourish: 'DESERT', flora: '150, 170, 90'
      };
    case 'BARREN':
      return {
        skyTop: '#0a0a12', skyMid: '#23232f', horizon: '#5a5a6e',
        glow: 'rgba(190, 190, 210, 0.3)', haze: '160, 160, 180',
        ridges: ['#3a3a4a', '#26262f', '#131318'],
        flourish: 'NONE', flora: '120, 140, 120'
      };
    case 'GAS_GIANT':
    default:
      // Violet dusk — matches the legacy landed-band gradient language
      return {
        skyTop: '#120822', skyMid: '#2d1a3d', horizon: '#6a4a8a',
        glow: 'rgba(190, 140, 255, 0.4)', haze: '170, 120, 240',
        ridges: ['#3a2a4f', '#241a33', '#120c1c'],
        flourish: 'NONE', flora: '150, 130, 190'
      };
  }
}

/**
 * The five-axis context fed to the landed scene each frame. Sun type/color +
 * distance come from the component's own /system snapshot; habitability and
 * citadel level come from the dashboard's landed-planet data.
 */
interface LandedCtx {
  /** 0–100; undefined → neutral mid (treated as ~55) */
  habitability?: number;
  /** 0–5 citadel build level; 0/undefined → untouched wilderness */
  citadelLevel?: number;
  /** star.kind from the system snapshot (e.g. RED_DWARF, BLUE_GIANT) */
  starKind?: string;
  /** star.color hex; undefined → derived default warm yellow */
  starColor?: string;
  /** companion star color hex if the system is a binary */
  secondaryColor?: string;
  /** this planet's orbit_au (distance to sun); undefined → mid 0.5 */
  orbitAu?: number;
  /** number of moons orbiting THIS landed world (drives sky-moon count) */
  moons?: number;
  /** this world's orbital phase in degrees — seeds each moon's lit fraction */
  phaseDeg?: number;
  /** true when the world has a ring system — hints a ring on the largest moon */
  rings?: boolean;
  /** 0–9-ish size class of THIS world — biases moon sizes */
  sizeClass?: number;
  /** id of THIS landed world — seeds time-of-day + landform variant so two
   *  worlds in the same sector never share stale cached geometry. */
  landedPlanetId?: string;
  /** The OTHER bodies in this system (siblings) so the landed sky can show them
   *  as distant planets — matching the system/flight view. Excludes the landed
   *  world itself. Cosmetic; kept minimal (kind/size/palette/rings). */
  siblings?: { kind: string; sizeClass: number; hue: number; sat: number; rings: boolean }[];
}

// ---------------------------------------------------------------------------
// Time-of-day + landform variance — both seeded per (sector + planet) so each
// world reads differently and a night sky NEVER carries a sun.
// ---------------------------------------------------------------------------

type TimeOfDay = 'DAY' | 'DUSK' | 'DAWN' | 'NIGHT';

/** Stable hash of a string → uint32, folded into the splitmix seed so the
 *  landed planet id (a UUID) contributes deterministic entropy. */
function hashStr(s: string | undefined): number {
  if (!s) return 0;
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Seed a time-of-day for this world. Weighted so DAY/NIGHT dominate and the
 *  two twilight bands are rarer (they're the "special" looks). */
function timeOfDayFor(seed: number): TimeOfDay {
  const r = splitmix32(seed * 2246822519 + 101)();
  if (r < 0.40) return 'DAY';
  if (r < 0.58) return 'DUSK';
  if (r < 0.72) return 'DAWN';
  return 'NIGHT';
}

/** Day-coupling factors derived from the time of day:
 *  - bright: 0 (deep night) → 1 (full day); lerps palette light + star count
 *  - showSun: sun disc drawn at all (never at night)
 *  - sunHeight: 0 = on horizon (twilight) → 1 = high (noon)
 *  - warm: extra warm bias for the low twilight sun
 *  - moonProminence: how bright/large moons read (1 at night → faint by day) */
function todProfile(tod: TimeOfDay): {
  bright: number; showSun: boolean; sunHeight: number; warm: number; moonProminence: number;
} {
  switch (tod) {
    case 'DAY':   return { bright: 1.0,  showSun: true,  sunHeight: 1.0, warm: 0.0,  moonProminence: 0.22 };
    case 'DUSK':  return { bright: 0.45, showSun: true,  sunHeight: 0.12, warm: 1.0, moonProminence: 0.55 };
    case 'DAWN':  return { bright: 0.5,  showSun: true,  sunHeight: 0.14, warm: 0.7, moonProminence: 0.5 };
    case 'NIGHT': return { bright: 0.08, showSun: false, sunHeight: 0.0, warm: 0.0,  moonProminence: 1.0 };
  }
}

/** Landform variant ids per flourish family. The variant changes where water,
 *  flora and the citadel sit and how the ridges are composed. */
type Landform =
  | 'OCEAN_ISLAND' | 'OCEAN_CLIFF' | 'OCEAN_SHORELINE'
  | 'VOLC_CALDERA' | 'VOLC_PLAIN' | 'VOLC_ASHFIELD'
  | 'ICE_GLACIER' | 'ICE_FROZENSEA' | 'ICE_PEAKS'
  | 'DES_DUNES' | 'DES_MESA' | 'DES_SALTFLAT'
  | 'TER_HILLS' | 'TER_FORESTVALLEY' | 'TER_COAST'
  | 'MTN_PEAKS' | 'MTN_PLATEAU' | 'MTN_GORGE'
  | 'BAR_CRATER' | 'BAR_ROCKY';

function landformFor(flourish: LandedPalette['flourish'], seed: number): Landform {
  const r = splitmix32(seed * 3266489917 + 53)();
  switch (flourish) {
    case 'OCEANIC':     return r < 0.34 ? 'OCEAN_ISLAND'  : r < 0.67 ? 'OCEAN_CLIFF'   : 'OCEAN_SHORELINE';
    case 'VOLCANIC':    return r < 0.34 ? 'VOLC_CALDERA'  : r < 0.67 ? 'VOLC_PLAIN'    : 'VOLC_ASHFIELD';
    case 'ICE':         return r < 0.34 ? 'ICE_GLACIER'   : r < 0.67 ? 'ICE_FROZENSEA' : 'ICE_PEAKS';
    case 'DESERT':      return r < 0.34 ? 'DES_DUNES'     : r < 0.67 ? 'DES_MESA'      : 'DES_SALTFLAT';
    case 'TERRAN':      return r < 0.34 ? 'TER_HILLS'     : r < 0.67 ? 'TER_FORESTVALLEY' : 'TER_COAST';
    case 'MOUNTAINOUS': return r < 0.34 ? 'MTN_PEAKS'     : r < 0.67 ? 'MTN_PLATEAU'   : 'MTN_GORGE';
    default:            return r < 0.5  ? 'BAR_CRATER'    : 'BAR_ROCKY';
  }
}

/** True when this landform shows open water below the horizon (ocean variants
 *  + a couple of cross-type water variants). */
function landformHasWater(lf: Landform): boolean {
  return lf === 'OCEAN_ISLAND' || lf === 'OCEAN_CLIFF' || lf === 'OCEAN_SHORELINE' ||
         lf === 'ICE_FROZENSEA' || lf === 'TER_COAST';
}

/** Lerp a "#rrggbb" toward white (amt>0) or black (amt<0) by |amt| in 0..1. */
function shiftHex(hex: string, amt: number): string {
  const { r, g, b } = hexToRgb(hex);
  const tgt = amt >= 0 ? 255 : 0;
  const k = Math.min(1, Math.abs(amt));
  const ch = (c: number) => Math.round(c + (tgt - c) * k);
  const to2 = (n: number) => n.toString(16).padStart(2, '0');
  return `#${to2(ch(r))}${to2(ch(g))}${to2(ch(b))}`;
}

/** Warm a "#rrggbb" toward sunset orange by amt in 0..1. */
function warmHex(hex: string, amt: number): string {
  const { r, g, b } = hexToRgb(hex);
  const k = Math.min(1, Math.max(0, amt));
  const ch = (c: number, t: number) => Math.round(c + (t - c) * k * 0.5);
  const to2 = (n: number) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, '0');
  return `#${to2(ch(r, 255))}${to2(ch(g, 130))}${to2(ch(b, 60))}`;
}

/** Star-kind → corona character (relative corona scale + a hotness bias for the
 *  sky wash). Falls back to a sun-like profile for unknown kinds. */
function starProfile(kind?: string): { corona: number; hot: number } {
  const k = (kind || '').toUpperCase().replace('STARTYPE.', '');
  // Specific compound kinds FIRST so generic substrings (GIANT/WHITE/RED) can't
  // shadow them (e.g. RED_GIANT must not match the blue-GIANT branch, and
  // WHITE_DWARF must not match the plain-WHITE branch).
  if (k.includes('NEUTRON') || k.includes('PULSAR') || k.includes('WHITE_DWARF') || k.includes('WHITE DWARF'))
    return { corona: 0.6, hot: 0.85 };  // tiny but fierce
  if (k.includes('RED') && k.includes('GIANT'))
    return { corona: 1.4, hot: 0.35 };  // red giant — bloated but cool
  if (k.includes('BLUE') || k === 'O' || k === 'B')
    return { corona: 1.7, hot: 1.0 };   // blue giant — huge searing corona
  if (k.includes('RED') || k.includes('DWARF') || k === 'M')
    return { corona: 0.7, hot: 0.25 };  // red dwarf — small, cool ember
  if (k.includes('ORANGE') || k === 'K')
    return { corona: 0.85, hot: 0.4 };
  if (k.includes('YELLOW') || k === 'G' || k === 'SOL')
    return { corona: 1.0, hot: 0.55 };
  if (k.includes('WHITE') || k === 'A' || k === 'F')
    return { corona: 1.2, hot: 0.7 };
  if (k.includes('GIANT'))                // any other (yellow/orange) giant
    return { corona: 1.3, hot: 0.6 };
  return { corona: 1.0, hot: 0.55 };
}

// ---------------------------------------------------------------------------
// LANDED-SCENE CACHE — all DETERMINISTIC (seeded) geometry + static gradients
// are built ONCE per (sector/type/citadel/hab-bucket/size/star) signature and
// reused every frame. Only time-driven drift/twinkle/sway/pulse + the few
// genuinely animated gradients are recomputed per frame. This collapses the
// per-frame GC churn (≈6 PRNG re-seeds + dozens of array/gradient allocs) down
// to the unavoidable 3 ridge paths + small particle/cloud/twinkle math.
// ---------------------------------------------------------------------------

type RidgeLayer = { base: number; amp: number; speed: number; pts: number[]; color: string };
type StarSeed = { x: number; y: number; size: number; twPhase: number; twSpeed: number; baseAlpha: number };
// Plant silhouettes — chosen by planet type + landform so vegetation reads as
// PLANTS, never "power poles". PALM/REED = oceanic shore; GRASS/BUSH = terran;
// SHRUB = desert mesa; TUSSOCK = hardy mountain/ice.
type FloraKind = 'PALM' | 'REED' | 'GRASS' | 'BUSH' | 'SHRUB' | 'TUSSOCK';
type FloraSeed = {
  x: number; height: number; swayPhase: number; wob: number;
  kind: FloraKind; lean: number; blades: number;
};
type CitadelStructure = {
  x: number; bw: number; bh: number;
  windows: { dx: number; dy: number; warm: number }[];
  isOutpost: boolean; isSpire: boolean;
};
type CitadelLayout = { structures: CitadelStructure[]; beacon: boolean; cityX: number; maxH: number; struct: string; twinkleWindows: boolean };
type HazeSeed = { baseX: number; yFrac: number; w: number; speed: number };
// Atmospheric particle kinds → drive shape/colour/motion of the precomputed seeds.
// SPRAY = oceanic sea-mist at the waterline; MOTE = a few low ground motes (lush).
type ParticleKind = 'EMBER' | 'SNOW' | 'DUST' | 'SPRAY' | 'MOTE' | 'FAINT';
type ParticleSeed = { x: number; y: number; size: number; phase: number; speed: number; drift: number; warm: number };
type CloudSeed = { x: number; yFrac: number; w: number; hFrac: number; speed: number; alpha: number };
type VolcFissure = { x: number; w: number; phase: number };
/** A moon hanging in the sky: position + radius + the phase geometry that draws
 *  its terminator (lit fraction + the orientation the shadow sweeps from). */
type MoonSeed = {
  x: number; y: number; r: number;
  /** 0 = new (dark) … 1 = full (fully lit) */
  illum: number;
  /** angle the lit limb faces (radians) — orients the crescent/gibbous shadow */
  lightAngle: number;
  /** subtle CRT tint "r,g,b" */
  tint: string;
  /** darker "r,g,b" for the mare mottling (precomputed from tint) */
  mareTint: string;
  /** static halo gradient (position/size/colour fixed; only alpha varies) */
  halo: CanvasGradient;
  /** draw a thin ring on this moon (only the largest, when the world has rings) */
  ring: boolean;
};
/** A precomputed wave crest line for the OCEANIC ocean: a horizontal band whose
 *  crests ripple via sine. yFrac is 0 (waterline) → 1 (foreground) so the draw
 *  can scale amplitude/spacing with depth. */
type WaveLine = {
  yFrac: number; amp: number; wavelength: number; speed: number; phase: number; alpha: number; lineW: number;
  // dynamism (NEW): per-crest drift direction, a slow amplitude "swell" that
  // breathes over time, and a second slower cross-swell so crests aren't all
  // parallel-uniform. All seeded once → deterministic; only t animates them.
  dir: number;          // +1 / -1 drift direction
  swellRate: number;    // slow breathing rate of the amplitude
  swellPhase: number;   // per-crest swell phase offset
  crossAmp: number;     // amplitude of the slow cross-swell
  crossWavelength: number; // wavelength of the cross-swell (much longer)
  // multi-scale detail (NEW): finer ripple layers ride between the big swells.
  fine: boolean;        // true → a thin fast ripple line (no filled face/foam)
  chopAmp: number;      // high-frequency chop amplitude base (scaled by weather)
  chopWavelength: number; // short wavelength of the chop ripple
};

/** A sibling planet rendered as a distant disc high in the landed sky. Position,
 *  size and per-kind colours are precomputed once (seeded) — only a tiny
 *  cosmetic shimmer varies per frame. */
type SkyPlanet = {
  x: number; y: number; r: number;
  treatment: Treatment;
  hue: number; sat: number;
  baseColor: string; bandColor: string; rimColor: string;
  rings: boolean; alpha: number;
};

/** Aquatic life: a seeded breach window. A dolphin-like back arcs up out of the
 *  sea and back down at (x, surfaceY) over [start, start+dur], periodically. */
type SeaCreature = {
  x: number;          // breach x on the sea
  surfaceFrac: number;// where on the water band it surfaces (0 horizon … 1 foreground)
  period: number;     // seconds between breaches (~15–40s)
  offset: number;     // phase offset so multiple creatures don't sync
  dur: number;        // breach duration (~1–2s)
  size: number;       // creature scale
  dir: number;        // +1 / -1 facing
};

// --- WORLD WEATHER (biome-agnostic) ----------------------------------------
// Deterministic per (world, day): the same world reads calm one day, stormy the
// next; two worlds on the same day differ. A single generic intensity scale
// (CALM…EXTREME) drives a shared factor set (WeatherFx) consumed by EVERY biome:
// sky darken/haze, precipitation, sun dimming, ambient-particle scaling — plus
// ocean-only wave fields used solely by water variants. weatherPhenomenaFor()
// maps a biome + tier to the right precip type + haze tint so the same engine
// renders rain, snow, ash, sand or dust depending on the world.
type WorldWeather = 'CALM' | 'CHOPPY' | 'ROUGH' | 'STORM' | 'HURRICANE';

type PrecipKind = 'none' | 'rain' | 'snow' | 'ash' | 'sand' | 'dust';

type WeatherFx = {
  tier: WorldWeather;       // generic intensity tier (0..4)
  tierN: number;            // 0..1 normalized intensity
  // GENERIC (all biomes)
  skyDarken: number;        // 0 (clear) … 1 (very dark) overlay on the sky
  sunDim: number;           // 0 (full sun) … 1 (sun fully veiled)
  hazeMul: number;          // multiplies the world's ambient haze strength
  hazeColor: string;        // "r, g, b" biome-tinted weather haze/overlay tint
  precip: PrecipKind;       // what falls/blows
  precipIntensity: number;  // 0..1 density of precipitation
  precipAngle: number;      // radians from vertical (wind-blown)
  windMul: number;          // generic wind strength (drift speeds)
  particleMul: number;      // scales the per-biome ambient particles (embers etc.)
  lightning: boolean;       // occasional full-scene flash (TERRAN/JUNGLE storms)
  // OCEAN-ONLY (consumed by water variants; harmless/neutral elsewhere)
  waveAmpMul: number;       // swell height multiplier
  waveCountMul: number;     // how many swell layers (× base)
  choppiness: number;       // extra high-frequency ripple amplitude (px)
  whitecapDensity: number;  // 0..1 fraction of swells that foam + fleck density
  foamMul: number;          // waterline / shore foam strength
  spraySpeedMul: number;    // sea-spray velocity multiplier
};

/** Sample the curved organic shoreline y at x (px). Combines a diagonal tilt with
 *  a couple of low-frequency sine arcs → a smooth bay / diagonal / cove edge.
 *  Plus a small per-x noise nudge from shoreProfile for an organic sand line. */
function shoreCurveYAt(
  x: number, w: number, h: number,
  curve: NonNullable<LandedCache['shoreCurve']>,
  shoreProfile: number[]
): number {
  const u = x / Math.max(1, w);
  let arc = 0;
  for (const a of curve.arcs) arc += Math.sin((x / a.wl) * Math.PI * 2 + a.ph) * a.a;
  const tilt = (u - 0.5) * curve.tilt;          // diagonal beach
  const idx = Math.min(shoreProfile.length - 1, Math.round(x / 8));
  const noise = (shoreProfile[idx] - 0.5) * 0.02; // tiny organic jitter
  return h * (curve.baseFrac + tilt) - (arc + noise) * curve.amp;
}

/** Map a biome (flourish) + intensity tier → the precipitation kind + haze tint
 *  that reads right for that world. Pure lookup; no randomness. tierN is 0..1. */
function weatherPhenomenaFor(
  flourish: LandedPalette['flourish'],
  tier: WorldWeather,
  tierN: number
): { precip: PrecipKind; hazeColor: string; lightning: boolean; sunDimBias: number } {
  const heavy = tier === 'STORM' || tier === 'HURRICANE';
  const mid = tier === 'ROUGH' || heavy;
  switch (flourish) {
    case 'OCEANIC':
      // rain only at storm+; storm-grey haze.
      return { precip: heavy ? 'rain' : 'none', hazeColor: '150, 165, 185', lightning: false, sunDimBias: 0 };
    case 'DESERT':
      // dust haze building to a SANDSTORM; warm orange tint; strong sun-dim high up.
      return { precip: mid ? 'sand' : 'dust', hazeColor: '210, 165, 95', lightning: false, sunDimBias: 0.25 };
    case 'ICE':
      // snow building to a BLIZZARD/whiteout; white haze.
      return { precip: tierN > 0.2 ? 'snow' : 'none', hazeColor: '225, 235, 245', lightning: false, sunDimBias: 0.1 };
    case 'VOLCANIC':
      // ash motes + embers → EMBER-STORM; dark smoky-red haze.
      return { precip: tierN > 0.2 ? 'ash' : 'dust', hazeColor: '120, 70, 60', lightning: false, sunDimBias: 0.2 };
    case 'TERRAN':
      // rain → THUNDERSTORM with lightning at storm+; dark grey haze.
      return { precip: heavy ? 'rain' : (mid ? 'rain' : 'none'), hazeColor: '140, 150, 165', lightning: heavy, sunDimBias: 0.05 };
    case 'MOUNTAINOUS':
      // wind-driven snow up high; cool grey-white haze.
      return { precip: mid ? 'snow' : 'none', hazeColor: '200, 210, 220', lightning: false, sunDimBias: 0.1 };
    default: // NONE → barren/gas/unknown: dust storm.
      return { precip: tierN > 0.3 ? 'dust' : 'none', hazeColor: '160, 150, 140', lightning: false, sunDimBias: 0.1 };
  }
}

/** Seed a world-weather tier for this world+day and resolve its generic factor
 *  set, then overlay the biome-specific precip + haze via weatherPhenomenaFor. */
function weatherFor(
  worldSeed: number,
  dayBucket: number,
  flourish: LandedPalette['flourish']
): WeatherFx {
  const r = splitmix32((worldSeed ^ (dayBucket >>> 0)) >>> 0 || 1)();
  // weighted: most days are mild; storms/extremes are the rare drama.
  let tier: WorldWeather;
  if (r < 0.34) tier = 'CALM';
  else if (r < 0.64) tier = 'CHOPPY';
  else if (r < 0.84) tier = 'ROUGH';
  else if (r < 0.95) tier = 'STORM';
  else tier = 'HURRICANE';
  const tierIdx = { CALM: 0, CHOPPY: 1, ROUGH: 2, STORM: 3, HURRICANE: 4 }[tier];
  const tierN = tierIdx / 4;
  // generic intensity ramps (shared across biomes)
  const skyDarken = [0, 0.05, 0.18, 0.42, 0.6][tierIdx];
  const hazeMul = [0.8, 1.0, 1.5, 2.2, 3.0][tierIdx];
  const precipIntensity = [0, 0.1, 0.4, 0.7, 1.0][tierIdx];
  const precipAngle = [0, 0.05, 0.18, 0.45, 0.8][tierIdx];
  const windMul = [0.7, 1.0, 1.3, 1.7, 2.2][tierIdx];
  const particleMul = [0.7, 1.0, 1.4, 1.9, 2.4][tierIdx];
  // ocean-only ramps
  const waveAmpMul = [0.55, 0.9, 1.35, 1.8, 2.3][tierIdx];
  const waveCountMul = [0.8, 1.1, 1.3, 1.5, 1.7][tierIdx];
  const choppiness = [0, 2.5, 5, 9, 14][tierIdx];
  const whitecapDensity = [0.05, 0.3, 0.6, 0.85, 1.0][tierIdx];
  const foamMul = [0.6, 1.0, 1.5, 2.0, 2.6][tierIdx];
  const spraySpeedMul = [0.7, 1.0, 1.3, 1.7, 2.2][tierIdx];

  const phen = weatherPhenomenaFor(flourish, tier, tierN);
  const sunDim = Math.min(1, skyDarken * 0.8 + phen.sunDimBias * tierN);
  return {
    tier, tierN, skyDarken, sunDim, hazeMul, hazeColor: phen.hazeColor,
    precip: phen.precip, precipIntensity, precipAngle, windMul, particleMul,
    lightning: phen.lightning,
    waveAmpMul, waveCountMul, choppiness, whitecapDensity, foamMul, spraySpeedMul,
  };
}

interface LandedCache {
  key: string;
  ctx: CanvasRenderingContext2D;
  horizonY: number;
  // resolved axes
  habN: number;
  citadel: number;
  prox: number;
  sc: { r: number; g: number; b: number };
  profile: { corona: number; hot: number };
  flourish: LandedPalette['flourish'];
  flora: number[]; // parsed "r,g,b"
  haze: string;
  // time-of-day + landform (NEW)
  tod: TimeOfDay;
  todBright: number;        // 0 (night) → 1 (day)
  showSun: boolean;
  landform: Landform;
  hasWater: boolean;
  waterTopY: number;        // y where the ocean begins (varies by landform)
  landBaseFrac: number;     // ridge base lift for the variant
  citadelOnWater: boolean;  // suppress citadel/flora that would float on water
  // reflection anchor (sun OR brightest moon) for the water glitter column
  reflX: number; reflTint: string;
  // sun anchor
  sunX: number; sunY: number; sunR: number; coronaR: number;
  // moons hanging in the sky (NEW)
  moons: MoonSeed[];
  moonProminence: number;
  // sibling planets shown as DISTANT discs in the sky (matches the system view).
  skyPlanets: SkyPlanet[];
  // ocean wave lines (NEW; OCEANIC / water variants only)
  waves: WaveLine[];
  // world weather (ALL biomes) + seeded precipitation streak/flake positions.
  weather: WeatherFx | null;
  precipSeeds: { x: number; y: number; len: number; speed: number; alpha: number; size: number }[];
  // aquatic life: seeded breach windows for a creature surfacing on the sea.
  creatures: SeaCreature[];
  // foreground SHORE (water variants only — replaces the 3 ridges on water worlds)
  shoreY: number;            // base y of the near shore the player stands on
  shoreColor: string;        // foreground landform fill
  shoreFoamColor: string;    // foam lip where land meets water
  shoreProfile: number[];    // precomputed undulation noise (0..1) for the shore edge
  // CURVED organic shoreline shape: a seeded smooth curve (bay/diagonal/cove)
  // sampled across the width. null for OCEAN_CLIFF (it uses the headland instead).
  shoreCurve: { amp: number; baseFrac: number; tilt: number; arcs: { wl: number; a: number; ph: number }[] } | null;
  // distant landmasses at the horizon: 0, 1, or 2 irregular off-centre silhouettes.
  farLands: { cx: number; halfW: number; peak: number; pts: number[]; color: string }[];
  // OCEAN_CLIFF only: a foreground SIDE headland (one seeded side is solid land
  // rising to a clifftop plateau; the open side reveals the sea). null otherwise.
  headland: {
    side: 'left' | 'right';  // which side is land
    landFrac: number;        // width of the land mass as a fraction of w (0.35..0.45)
    topProfile: number[];    // clifftop surface noise (0..1) across the land x-window
    topColor: string;        // clifftop plateau fill (lighter)
    faceColor: string;       // vertical cliff face fill (darker)
    edgeColor: string;       // highlight stroke along the clifftop edge
    plateauY: number;        // base y of the clifftop plateau
  } | null;
  hasCompanion: boolean; c2x: number; c2y: number; c2r: number; c2: { r: number; g: number; b: number };
  // ridge geometry (noise precomputed; drifted profile recomputed per frame)
  layers: RidgeLayer[];
  period: number;
  ridgeColors: string[];
  // STATIC gradients (coordinate + colour fixed for the session)
  skyGrad: CanvasGradient;
  washGrad: CanvasGradient | null;
  coronaGrad: CanvasGradient;
  discGrad: CanvasGradient;
  companionCorona: CanvasGradient | null;
  glowGrad: CanvasGradient;
  starHueGlow: CanvasGradient;
  waterBand: CanvasGradient | null;
  // layouts
  stars: StarSeed[];
  flouraSeeds: FloraSeed[];
  citadelLayout: CitadelLayout | null;
  haze3: HazeSeed[];
  hazeStrength: number;
  particles: ParticleSeed[];
  particleKind: ParticleKind;
  clouds: CloudSeed[];
  cloudTint: string; // "r, g, b"
  volcFissures: VolcFissure[];
  desertBands: boolean;
  iceSheen: boolean;
}

let landedCache: LandedCache | null = null;

/** Per-flourish atmospheric particle character. Context-appropriate + LOW —
 *  never the up-drifting sky floaters that read as shooting stars. */
function particleKindFor(flourish: LandedPalette['flourish'], habN: number, hasWater: boolean): ParticleKind {
  if (flourish === 'VOLCANIC') return 'EMBER';      // embers hugging the fissures
  if (flourish === 'ICE') return 'SNOW';            // snow falling
  if (flourish === 'DESERT') return 'DUST';         // low blowing dust
  if (flourish === 'OCEANIC' || hasWater) return 'SPRAY'; // sea spray at the waterline
  // a few LOW ground-level motes only for living worlds — never on barren/gas.
  if (flourish === 'TERRAN' ||
      (flourish === 'MOUNTAINOUS' && habN > 0.4) ||
      (habN > 0.55 && flourish !== 'NONE'))
    return 'MOTE';
  return 'FAINT';
}

/** Build (or rebuild) the landed-scene cache. Pure deterministic precompute —
 *  the only ctx use is creating static gradient objects (bound to this canvas
 *  context but reusable across frames). */
function buildLandedCache(
  ctx: CanvasRenderingContext2D,
  key: string,
  w: number,
  h: number,
  sectorId: number,
  pal: LandedPalette,
  env?: LandedCtx
): LandedCache {
  const horizonY = h * 0.58;
  const seed = (sectorId >>> 0) || 1;
  // Per-world seed: fold the landed planet id into the sector seed so two worlds
  // in one sector get distinct time-of-day + landform (and distinct cached geom).
  const worldSeed = ((seed ^ hashStr(env?.landedPlanetId)) >>> 0) || seed || 1;
  // UTC day index — sea-state weather is deterministic per (world, day).
  const dayBucket = Math.floor(Date.now() / 86400000);

  const hab = env && typeof env.habitability === 'number'
    ? Math.max(0, Math.min(100, env.habitability)) : 55;
  const habN = hab / 100;
  const citadel = env ? Math.max(0, Math.min(5, Math.round(env.citadelLevel || 0))) : 0;
  const orbitAu = env && typeof env.orbitAu === 'number' && env.orbitAu > 0 ? env.orbitAu : 0.5;
  const ORBIT_NEAR = 0.15, ORBIT_FAR = 1.0;
  const prox = Math.max(0.05, 1 - Math.min(1, Math.max(0, (orbitAu - ORBIT_NEAR) / (ORBIT_FAR - ORBIT_NEAR))));
  const profile = starProfile(env?.starKind);

  // --- TIME OF DAY (seeded; couples sky brightness + sun visibility) ---
  const tod = timeOfDayFor(worldSeed);
  const todp = todProfile(tod);
  const todBright = todp.bright;
  const showSun = todp.showSun;
  // Star color, then warm it for twilight (low-sun orange wash).
  let sc = hexToRgb(env?.starColor || '#ffd27a');
  if (todp.warm > 0) {
    sc = {
      r: Math.min(255, Math.round(sc.r + (255 - sc.r) * todp.warm * 0.5)),
      g: Math.round(sc.g + (140 - sc.g) * todp.warm * 0.4),
      b: Math.round(sc.b + (60 - sc.b) * todp.warm * 0.4),
    };
  }

  // --- LANDFORM VARIANT (seeded; drives water placement + ridge composition) ---
  const landform = landformFor(pal.flourish, worldSeed);
  const hasWater = landformHasWater(landform);
  // Where the water surface starts + how the land sits, per variant.
  let waterTopY = horizonY;
  let landBaseFrac = 0;        // shift ridge base lower(+)/higher(-)
  let citadelOnWater = false;  // true → push city onto the small land mass
  // Water worlds are OCEAN-DOMINANT: the sea fills the band from the horizon down
  // to a LOW foreground shore. waterTopY sits at (or near) the horizon for every
  // water variant so the sea is large; the foreground shore (shoreY, below) is the
  // only land drawn — the 3 parallax ridges are skipped on water worlds.
  let shoreY = h * 0.84;       // base y of the near shore the player stands on
  if (hasWater) {
    if (landform === 'OCEAN_CLIFF') {
      // standing high on a clifftop looking out/down to a big sea below.
      waterTopY = h * 0.56; shoreY = h * 0.80; landBaseFrac = -0.05;
    } else if (landform === 'OCEAN_ISLAND') {
      // open sea to the horizon with a distant island; a near shore underfoot.
      waterTopY = h * 0.56; shoreY = h * 0.86; landBaseFrac = 0.18; citadelOnWater = true;
    } else { // SHORELINE / ICE_FROZENSEA / TER_COAST — beach in the foreground, sea above
      waterTopY = h * 0.56; shoreY = h * 0.84; landBaseFrac = 0.04;
    }
  } else if (landform === 'VOLC_CALDERA' || landform === 'MTN_PEAKS' || landform === 'ICE_PEAKS') {
    landBaseFrac = -0.08; // dramatic high rim/peaks
  } else if (landform === 'DES_SALTFLAT' || landform === 'VOLC_PLAIN' || landform === 'MTN_PLATEAU' || landform === 'BAR_ROCKY') {
    landBaseFrac = 0.06;  // flat, low
  } else if (landform === 'DES_MESA' || landform === 'MTN_GORGE' || landform === 'BAR_CRATER') {
    landBaseFrac = 0.0;
  }

  // --- Sky gradient (static; brightness coupled to time of day) ---
  const skyTop = shiftHex(pal.skyTop, (todBright - 0.5) * 0.55 + todp.warm * 0.1);
  const skyMid = warmHex(shiftHex(pal.skyMid, (todBright - 0.5) * 0.6), todp.warm * 0.6);
  const skyHor = warmHex(shiftHex(pal.horizon, (todBright - 0.5) * 0.45), todp.warm);
  const skyGrad = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
  skyGrad.addColorStop(0, skyTop);
  skyGrad.addColorStop(0.6, skyMid);
  skyGrad.addColorStop(1, skyHor);

  // --- Star-tinted sky wash (static) ---
  const washA = 0.05 + prox * profile.hot * 0.18;
  let washGrad: CanvasGradient | null = null;
  if (washA > 0.001) {
    washGrad = ctx.createLinearGradient(0, horizonY * 0.4, 0, horizonY * 1.1);
    washGrad.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);
    washGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${washA.toFixed(3)})`);
  }

  // --- Sun anchor (seeded once) ---
  // sunHeight from the time of day: high at noon, hugging the horizon at dawn/dusk.
  const anchorRng = splitmix32(worldSeed * 911 + 3);
  const sunX = w * (0.18 + anchorRng() * 0.64);
  // sunHeight 1 → high in the sky (small horizonY factor); 0 → sitting on horizon.
  const sunY = horizonY * (0.86 - todp.sunHeight * 0.62) + (anchorRng() - 0.5) * horizonY * 0.08;
  // Twilight sun reads larger/softer near the horizon.
  const sizeBias = tod === 'DUSK' || tod === 'DAWN' ? 1.35 : 1.0;
  const sunR = Math.max(6, Math.min(Math.min(w, h) * 0.13, Math.min(w, h) * (0.018 + prox * 0.06) * profile.corona * sizeBias));
  const coronaR = Math.min(Math.hypot(w, h) * 0.55, sunR * (5 + prox * 4) * profile.corona);

  // corona (static gradient)
  const coronaGrad = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, coronaR);
  coronaGrad.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(0.35 + prox * 0.35).toFixed(3)})`);
  coronaGrad.addColorStop(0.35, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(0.12 + prox * 0.15).toFixed(3)})`);
  coronaGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);

  // bright core disc (static gradient)
  const discGrad = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR);
  const coreWhite = Math.round(160 + prox * 95);
  discGrad.addColorStop(0, `rgba(${Math.min(255, sc.r + coreWhite * 0.4)}, ${Math.min(255, sc.g + coreWhite * 0.4)}, ${Math.min(255, sc.b + coreWhite * 0.4)}, 0.98)`);
  discGrad.addColorStop(0.6, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0.95)`);
  discGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0.5)`);

  // companion sun (binary, static)
  let hasCompanion = false, c2x = 0, c2y = 0, c2r = 0;
  let c2 = { r: 0, g: 0, b: 0 };
  let companionCorona: CanvasGradient | null = null;
  if (env?.secondaryColor) {
    hasCompanion = true;
    c2 = hexToRgb(env.secondaryColor);
    c2x = sunX + sunR * 4.5 * (anchorRng() > 0.5 ? 1 : -1);
    c2y = sunY + sunR * 1.8;
    c2r = sunR * 0.55;
    companionCorona = ctx.createRadialGradient(c2x, c2y, 0, c2x, c2y, c2r * 4);
    companionCorona.addColorStop(0, `rgba(${c2.r}, ${c2.g}, ${c2.b}, 0.4)`);
    companionCorona.addColorStop(1, `rgba(${c2.r}, ${c2.g}, ${c2.b}, 0)`);
  } else {
    // consume the same RNG draw the companion branch would have so downstream
    // seeded draws stay identical whether or not a companion exists.
    anchorRng();
  }

  // --- MOONS in the sky (seeded; phase terminator computed once) ---
  const moonCount = env && typeof env.moons === 'number' ? Math.max(0, Math.min(3, Math.round(env.moons))) : 0;
  const moonProminence = todp.moonProminence;
  const moons: MoonSeed[] = [];
  if (moonCount > 0) {
    const mRng = splitmix32(worldSeed * 2654435761 + 777);
    const basePhase = (typeof env?.phaseDeg === 'number' ? env.phaseDeg : 0) * Math.PI / 180;
    const szBias = env && typeof env.sizeClass === 'number' ? 0.7 + Math.min(1, env.sizeClass / 9) * 0.7 : 1.0;
    let biggestIdx = 0, biggestR = 0;
    for (let i = 0; i < moonCount; i++) {
      // Keep moons out of the sun's immediate halo and spread across the sky.
      const mx = w * (0.12 + mRng() * 0.76);
      const my = horizonY * (0.12 + mRng() * 0.4);
      const r = Math.max(7, Math.min(w, h) * (0.024 + mRng() * 0.03) * szBias * (i === 0 ? 1.25 : 0.85));
      // Illumination fraction: derive from the world phase + a per-moon offset so
      // multiple moons show different phases (crescent / gibbous / full).
      const ill = 0.5 + 0.5 * Math.cos(basePhase + i * 2.0 + mRng() * Math.PI);
      const lightAngle = basePhase + i * 0.7 + (mRng() - 0.5) * 0.6;
      // Pale CRT tint, slightly per-moon varied.
      const warmth = mRng();
      const tint = warmth > 0.6
        ? '210, 200, 180'
        : warmth > 0.3 ? '200, 210, 225' : '190, 200, 215';
      // Precompute the darker mare tint + the static halo gradient (all inputs
      // are cached constants — only the per-frame alpha "breathing" varies).
      const mareTint = tint.split(',').map((s) => Math.max(0, parseInt(s, 10) - 40)).join(', ');
      const halo = ctx.createRadialGradient(mx, my, r * 0.6, mx, my, r * 2.6);
      halo.addColorStop(0, `rgba(${tint}, ${(0.18 * moonProminence).toFixed(3)})`);
      halo.addColorStop(1, `rgba(${tint}, 0)`);
      moons.push({ x: mx, y: my, r, illum: ill, lightAngle, tint, mareTint, halo, ring: false });
      if (r > biggestR) { biggestR = r; biggestIdx = i; }
    }
    if (env?.rings && moons.length > 0) moons[biggestIdx].ring = true;
  }

  // --- Reflection anchor for water glitter: the sun by day, else brightest moon. ---
  let reflX = sunX;
  let reflTint = `${sc.r}, ${sc.g}, ${sc.b}`;
  if (!showSun && moons.length > 0) {
    let best = moons[0];
    for (const m of moons) if (m.illum > best.illum) best = m;
    reflX = best.x;
    reflTint = best.tint;
  }

  // --- Horizon glow (static) ---
  const gx = w * (0.25 + anchorRng() * 0.5);
  const glowGrad = ctx.createRadialGradient(gx, horizonY, 0, gx, horizonY, Math.max(w, h) * (0.4 + prox * 0.18));
  glowGrad.addColorStop(0, pal.glow);
  glowGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
  const starHueGlow = ctx.createRadialGradient(sunX, horizonY, 0, sunX, horizonY, Math.max(w, h) * 0.35);
  starHueGlow.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(prox * profile.hot * 0.22).toFixed(3)})`);
  starHueGlow.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);

  // --- WATER surface gradient + animated wave lines (water variants only) ---
  // The flat teal band is replaced by a real ocean: a darker depth gradient plus
  // layered wave crest-lines (precomputed; the crests ripple per frame).
  let waterBand: CanvasGradient | null = null;
  const waves: WaveLine[] = [];
  // Shore + far-island artefacts (water variants only). Precomputed once — the
  // shore edge undulation and the distant island silhouette are seeded here and
  // never re-seeded per frame; only sine drift moves the waves/foam.
  let shoreColor = '';
  let shoreFoamColor = '';
  const shoreProfile: number[] = [];
  let shoreCurve: LandedCache['shoreCurve'] = null;
  const farLands: LandedCache['farLands'] = [];
  let headland: LandedCache['headland'] = null;
  const creatures: SeaCreature[] = [];

  // --- WORLD WEATHER (ALL biomes) — deterministic per (world, day). dayBucket is
  // folded into the cache key so it rebuilds daily. Drives sky/haze/precip/sun-dim
  // for every landform; water variants additionally consume the wave fields. We
  // skip weather for GAS-default skies with no surface? No — even barren/gas get a
  // dust haze. Only suppress on the unknown-violet 'NONE' if it has no ground? It
  // still has ridges, so weather applies everywhere. ---
  const weather: WeatherFx = weatherFor(worldSeed, dayBucket, pal.flourish);
  const wx = weather;
  // Seeded precipitation positions (rain streaks / snow flakes / ash / sand / dust
  // motes). Count scales with the day's intensity; capped for perf. Drawn per
  // frame by the shared drawPrecipitation renderer.
  const precipSeeds: LandedCache['precipSeeds'] = [];
  if (weather.precip !== 'none' && weather.precipIntensity > 0) {
    const pkMax = weather.precip === 'snow' ? 120
      : weather.precip === 'ash' ? 70
      : weather.precip === 'dust' ? 60
      : weather.precip === 'sand' ? 110
      : 90; // rain
    const pCountW = Math.max(8, Math.round(pkMax * weather.precipIntensity));
    const pwRng = splitmix32(worldSeed * 7919 + 401);
    for (let i = 0; i < pCountW; i++) {
      precipSeeds.push({
        x: pwRng(),                 // 0..1 across an extended span
        y: pwRng(),                 // 0..1 down the scene (start offset)
        len: 8 + pwRng() * 16,
        speed: 0.7 + pwRng() * 0.7,
        alpha: 0.10 + pwRng() * 0.18,
        size: 0.7 + pwRng() * 1.6,  // flake/mote radius
      });
    }
  }

  if (hasWater) {
    const bandTop = waterTopY;
    // The sea band now spans most of the lower scene (horizon → shore). Make the
    // depth gradient OPAQUE blue-teal so it reads unmistakably as water, not a
    // faint wash over the sky. Brighter near the waterline (sky reflection),
    // deepening to a dark abyssal blue at the foreground.
    waterBand = ctx.createLinearGradient(0, bandTop, 0, h);
    const surf = todBright > 0.3
      ? { r: Math.round(40 + sc.r * 0.18), g: Math.round(120 + sc.g * 0.18), b: Math.round(150 + sc.b * 0.15) }
      : { r: 28, g: 70, b: 104 };
    waterBand.addColorStop(0, `rgba(${surf.r}, ${surf.g}, ${surf.b}, 0.92)`);
    waterBand.addColorStop(0.4, 'rgba(18, 78, 116, 0.95)');
    waterBand.addColorStop(0.8, 'rgba(10, 46, 78, 0.97)');
    waterBand.addColorStop(1, 'rgba(5, 24, 46, 0.98)');

    // wave crest-lines: MANY lines, clearly visible, denser near the horizon and
    // larger & farther apart toward the foreground (perspective). Each crest gets a
    // bright top stroke + a darker trough stroke drawn just below for definition so
    // the surface reads as moving water, not a flat tint.
    const wRng = splitmix32(worldSeed * 1597 + 91);
    // MULTI-SCALE sea: big filled SWELLS + finer/faster ripple lines woven between
    // them. Swell height/count scale by the day's WEATHER (calm → small & few,
    // hurricane → tall & many). Each swell varies in size + carries a long
    // cross-swell + its own vertical-bob rate; weather adds high-freq chop.
    const baseSwells = 11;
    const swellCount = Math.max(6, Math.round(baseSwells * wx.waveCountMul));
    for (let i = 0; i < swellCount; i++) {
      const lin = i / (swellCount - 1);          // 0 = waterline … 1 = foreground
      const f = lin * lin;                       // perspective spacing
      const sizeJitter = 0.6 + wRng() * 0.9;     // each swell a different size
      waves.push({
        yFrac: f,
        amp: (2 + f * 16) * sizeJitter * wx.waveAmpMul, // weather-scaled swell height
        wavelength: (90 + f * 320) * (0.6 + wRng() * 0.9), // long, varied crests
        speed: (0.5 + f * 1.4) * (0.7 + wRng() * 0.7) * (0.85 + wx.waveAmpMul * 0.25),
        phase: wRng() * Math.PI * 2,
        alpha: (0.5 + f * 0.4),
        lineW: 1 + f * 2.6,
        dir: wRng() < 0.78 ? 1 : -1,
        swellRate: 0.25 + wRng() * 0.5,          // each heaves at its own pace
        swellPhase: wRng() * Math.PI * 2,
        crossAmp: (2 + f * 7) * (0.5 + wRng() * 0.9),
        crossWavelength: (160 + f * 360) * (0.7 + wRng() * 0.7),
        fine: false,
        chopAmp: (0.6 + f * 1.2) * wx.choppiness,
        chopWavelength: (10 + f * 24) * (0.7 + wRng() * 0.6),
      });
    }
    // FINE ripple lines — thin, fast, between the swells; denser in rougher seas.
    const fineCount = Math.round((10 + 8 * wx.waveAmpMul));
    for (let i = 0; i < fineCount; i++) {
      const f = wRng();                          // scattered across the band
      waves.push({
        yFrac: 0.1 + f * 0.88,
        amp: (1 + f * 3) * (0.6 + wx.waveAmpMul * 0.4),
        wavelength: (24 + f * 70) * (0.7 + wRng() * 0.6),
        speed: (1.2 + f * 2.2) * (0.8 + wRng() * 0.6),
        phase: wRng() * Math.PI * 2,
        alpha: 0.10 + f * 0.16,
        lineW: 0.6 + f * 0.8,
        dir: wRng() < 0.7 ? 1 : -1,
        swellRate: 0.4 + wRng() * 0.9,
        swellPhase: wRng() * Math.PI * 2,
        crossAmp: (1 + f * 2) * (0.5 + wRng() * 0.6),
        crossWavelength: (80 + f * 180),
        fine: true,
        chopAmp: (0.4 + f * 0.8) * wx.choppiness,
        chopWavelength: (8 + f * 16),
      });
    }

    // --- foreground SHORE colour + foam, per water variant ---
    if (landform === 'ICE_FROZENSEA') {
      shoreColor = 'rgb(214, 230, 240)';   // pale ice shelf
      shoreFoamColor = 'rgba(240, 250, 255, 1)';
    } else if (landform === 'TER_COAST') {
      const fc = pal.flora.split(',').map((s) => parseInt(s.trim(), 10));
      shoreColor = `rgb(${fc[0]}, ${fc[1]}, ${fc[2]})`; // green coastal land
      shoreFoamColor = 'rgba(225, 245, 250, 1)';
    } else if (landform === 'OCEAN_CLIFF') {
      shoreColor = 'rgb(48, 44, 52)';      // dark sharp rock edge
      shoreFoamColor = 'rgba(210, 235, 245, 1)';
    } else { // OCEAN_ISLAND / OCEAN_SHORELINE — sandy/rocky beach
      shoreColor = 'rgb(150, 132, 96)';
      shoreFoamColor = 'rgba(235, 248, 250, 1)';
    }

    // shore edge undulation (precomputed noise; sampled across the width)
    const shRng = splitmix32(worldSeed * 2089 + 53);
    const shorePts = Math.ceil(w / 8) + 2;
    for (let i = 0; i < shorePts; i++) shoreProfile.push(shRng());

    // CURVED ORGANIC SHORELINE (non-cliff water variants). The sand boundary is a
    // smooth seeded curve — a bay, a diagonal beach, or a cove — built from a
    // diagonal TILT plus a couple of low-frequency sine arcs (NOT a flat line).
    if (landform !== 'OCEAN_CLIFF') {
      const scRng = splitmix32(worldSeed * 9173 + 61);
      const arcs = [
        { wl: w * (0.7 + scRng() * 0.8), a: 0.10 + scRng() * 0.18, ph: scRng() * Math.PI * 2 },
        { wl: w * (0.3 + scRng() * 0.4), a: 0.04 + scRng() * 0.10, ph: scRng() * Math.PI * 2 },
      ];
      shoreCurve = {
        amp: h * (0.10 + scRng() * 0.10),       // how deep the bay/cove cuts
        baseFrac: 0.80 + scRng() * 0.08,        // mean shore height (frac of h)
        tilt: (scRng() - 0.5) * 0.22,           // diagonal beach: -0.11..+0.11 of h across w
        arcs,
      };
    }

    // DISTANT LANDMASSES — 0, 1, or 2 irregular, OFF-CENTRE silhouettes at the
    // horizon (never a single centred bell). Seeded position/width/height/shape so
    // they read as real far land, sometimes partly off-screen.
    const landRoll = (landform === 'OCEAN_ISLAND')
      ? (1 + (shRng() < 0.45 ? 1 : 0))          // island world: 1 or 2 landmasses
      : (shRng() < 0.55 ? (shRng() < 0.35 ? 2 : 1) : 0); // others: 0/1/2
    if (landRoll > 0) {
      const lRng = splitmix32(worldSeed * 3371 + 97);
      for (let li = 0; li < landRoll; li++) {
        const cx = w * (lRng() * 1.2 - 0.1);    // -0.1..1.1 → can sit partly off-screen
        const halfW = w * (0.06 + lRng() * 0.20);
        const peak = h * (0.025 + lRng() * 0.07) * (li === 0 ? 1 : 0.7);
        const ipts = 10 + Math.floor(lRng() * 12);
        const pts: number[] = [];
        for (let i = 0; i < ipts; i++) pts.push(lRng());
        const dim = li === 0 ? 0.9 : 0.7;       // farther/secondary masses fainter
        const ic = todBright > 0.3
          ? `rgba(40, 60, 78, ${(0.85 * dim).toFixed(2)})`
          : `rgba(24, 36, 54, ${(0.9 * dim).toFixed(2)})`;
        farLands.push({ cx, halfW, peak, pts, color: ic });
      }
    }

    // OCEAN_CLIFF: a foreground SIDE HEADLAND. One seeded side is a solid land mass
    // rising to a clifftop plateau; the open side reveals the dominant sea. The
    // clifftop is the surface structures stand on (frontProfile is set over the
    // headland x-window at draw time; flora/citadel are clamped to that side).
    if (landform === 'OCEAN_CLIFF') {
      const hRng = splitmix32(worldSeed * 4129 + 131);
      const side: 'left' | 'right' = hRng() < 0.5 ? 'left' : 'right';
      const landFrac = 0.35 + hRng() * 0.10;       // 35%..45% of the width
      // clifftop plateau height: a touch of seeded variety in the h*0.45..0.60 band
      const plateauY = h * (0.45 + hRng() * 0.15);
      // clifftop surface noise across the land x-window
      const topProfile: number[] = [];
      const topPts = Math.max(8, Math.round(w * landFrac / 8) + 2);
      for (let i = 0; i < topPts; i++) topProfile.push(hRng());
      // tints from the palette ridge colours: plateau lighter, face darker.
      const baseRidge = hexToRgb(pal.ridges[2] || pal.ridges[0] || '#5a5560');
      const lighten = (c: { r: number; g: number; b: number }, k: number) => ({
        r: Math.min(255, Math.round(c.r + (255 - c.r) * k)),
        g: Math.min(255, Math.round(c.g + (255 - c.g) * k)),
        b: Math.min(255, Math.round(c.b + (255 - c.b) * k)),
      });
      const darken = (c: { r: number; g: number; b: number }, k: number) => ({
        r: Math.round(c.r * (1 - k)), g: Math.round(c.g * (1 - k)), b: Math.round(c.b * (1 - k)),
      });
      const top = lighten(baseRidge, 0.18 + todBright * 0.12);
      const face = darken(baseRidge, 0.42);
      const edge = lighten(baseRidge, 0.5 + todBright * 0.2);
      headland = {
        side, landFrac, topProfile, plateauY,
        topColor: `rgb(${top.r}, ${top.g}, ${top.b})`,
        faceColor: `rgb(${face.r}, ${face.g}, ${face.b})`,
        edgeColor: `rgba(${edge.r}, ${edge.g}, ${edge.b}, 0.7)`,
      };
    }

    // --- AQUATIC LIFE: 1–2 sea creatures with seeded breach windows ---
    const crRng = splitmix32(worldSeed * 5179 + 211);
    const crCount = 1 + (crRng() < 0.5 ? 1 : 0);
    for (let i = 0; i < crCount; i++) {
      // surface on the OPEN water — for the cliff, keep clear of the headland side.
      let xMin = w * 0.1, xMax = w * 0.9;
      if (headland) {
        if (headland.side === 'left') xMin = w * (headland.landFrac + 0.05);
        else xMax = w * (1 - headland.landFrac - 0.05);
      }
      creatures.push({
        x: xMin + crRng() * (xMax - xMin),
        surfaceFrac: 0.35 + crRng() * 0.5,         // mid-to-near sea, not at the horizon
        period: 15 + crRng() * 25,                 // every ~15–40s
        offset: crRng() * 40,                      // desync the breaches
        dur: 1.0 + crRng() * 1.0,                  // ~1–2s breach
        size: 10 + crRng() * 10,
        dir: crRng() < 0.5 ? 1 : -1,
      });
    }
  } else {
    // NON-WATER worlds: a distant horizon feature must ALSO be off-centre +
    // irregular (the player flagged centred/symmetric silhouettes). Reuse the
    // farLands off-centre logic for far mountains / mesas / dunes sitting behind
    // the 3 noise ridges, palette-tinted and dimmed by distance. 0–2 masses.
    const lRng = splitmix32(worldSeed * 3371 + 97);
    const landRoll = lRng() < 0.5 ? (lRng() < 0.4 ? 2 : 1) : 0;
    if (landRoll > 0) {
      // distant land tint: darker shade of the world's mid ridge colour, hazed.
      const rc = hexToRgb(pal.ridges[1] || pal.ridges[0] || '#4a4652');
      const far = { r: Math.round(rc.r * 0.55 + 20), g: Math.round(rc.g * 0.55 + 24), b: Math.round(rc.b * 0.55 + 30) };
      for (let li = 0; li < landRoll; li++) {
        const cx = w * (lRng() * 1.2 - 0.1);      // off-centre, can run off-screen
        const halfW = w * (0.10 + lRng() * 0.26);
        const peak = h * (0.04 + lRng() * 0.10) * (li === 0 ? 1 : 0.7); // far peaks
        const ipts = 10 + Math.floor(lRng() * 12);
        const pts: number[] = [];
        for (let i = 0; i < ipts; i++) pts.push(lRng());
        const dim = li === 0 ? 0.8 : 0.6;
        const ic = `rgba(${far.r}, ${far.g}, ${far.b}, ${(0.8 * dim).toFixed(2)})`;
        farLands.push({ cx, halfW, peak, pts, color: ic });
      }
    }
  }

  // --- STARFIELD layout (twinkle stays per frame) ---
  // Mostly visible at NIGHT; sparse + dim by day. Stars never move vertically.
  const nightBoost = 0.25 + (1 - todBright) * 0.75; // 1.0 at night → 0.25 at noon
  const starCount = Math.round((8 + (1 - habN) * 70) * (0.4 + (1 - todBright) * 0.9));
  const sfRng = splitmix32(worldSeed * 2654435761 + 17);
  const stars: StarSeed[] = [];
  for (let i = 0; i < starCount; i++) {
    const x = sfRng() * w;
    const y = sfRng() * (horizonY * 0.92);
    const size = 0.3 + sfRng() * 0.9;
    const twSpeed = 0.6 + sfRng() * 1.4;
    const baseAlpha = (0.12 + sfRng() * 0.35) * (0.4 + (1 - habN) * 0.6) * nightBoost;
    stars.push({ x, y, size, twPhase: i, twSpeed, baseAlpha });
  }

  // --- SIBLING PLANETS as distant sky discs (matches the system/flight view) ---
  // Each sibling body from the /system snapshot is placed high in the sky, spread
  // out and kept clear of the sun + moons, sized by size_class and dimmed by the
  // "distance" haze. Per-kind colours mirror the flight scene's treatment vocab.
  const skyPlanets: SkyPlanet[] = [];
  const sibs = env?.siblings || [];
  if (sibs.length > 0) {
    const spRng = splitmix32(worldSeed * 6271 + 313);
    // sky band: upper region only (above the horizon, clear of the lower scene)
    const skyTopBand = horizonY * 0.10;
    const skyBotBand = horizonY * 0.46;
    const occupied: { x: number; y: number; r: number }[] = [];
    // exclusions: the sun + each moon (so siblings never overlap them)
    if (showSun) occupied.push({ x: sunX, y: sunY, r: sunR * 2.2 });
    for (const m of moons) occupied.push({ x: m.x, y: m.y, r: m.r * 2.2 });
    for (let i = 0; i < sibs.length; i++) {
      const s = sibs[i];
      const treatment = treatmentFor(s.kind);
      const r = Math.max(5, Math.min(w, h) * (0.012 + Math.min(9, s.sizeClass) / 9 * 0.022));
      // try a few seeded positions; pick the first that clears sun/moons/others
      let px = 0, py = 0, ok = false;
      for (let attempt = 0; attempt < 6; attempt++) {
        // spread across width in soft columns so siblings don't clump
        px = w * ((i + 0.5) / sibs.length) + (spRng() - 0.5) * (w / sibs.length) * 0.7;
        py = skyTopBand + spRng() * (skyBotBand - skyTopBand);
        ok = true;
        for (const o of occupied) {
          if (Math.hypot(px - o.x, py - o.y) < o.r + r * 1.8) { ok = false; break; }
        }
        if (ok) break;
      }
      px = Math.max(r + 4, Math.min(w - r - 4, px));
      occupied.push({ x: px, y: py, r: r * 1.8 });
      // per-kind colours from hue/sat (mirrors drawPlanetSurface treatment vocab)
      const hue = s.hue, sat = s.sat;
      let baseColor: string, bandColor: string, rimColor: string;
      if (treatment === 'GAS_GIANT') {
        baseColor = `hsl(${hue}, ${sat}%, 42%)`;
        bandColor = `hsla(${hue + 18}, ${sat}%, 30%, 0.85)`;   // banding
        rimColor = `hsla(${hue}, ${sat}%, 70%, 0.5)`;
      } else if (treatment === 'ICE') {
        baseColor = `hsl(${hue}, ${Math.max(8, sat - 20)}%, 78%)`; // pale
        bandColor = `hsla(${hue}, ${sat}%, 88%, 0.6)`;
        rimColor = `hsla(${hue}, 20%, 95%, 0.5)`;
      } else if (treatment === 'VOLCANIC') {
        baseColor = `hsl(${hue}, ${sat}%, 30%)`;
        bandColor = `hsla(20, 90%, 55%, 0.5)`;                  // ember glow
        rimColor = `hsla(30, 100%, 60%, 0.5)`;
      } else if (treatment === 'DESERT') {
        baseColor = `hsl(${hue}, ${sat}%, 52%)`;
        bandColor = `hsla(${hue - 12}, ${sat}%, 44%, 0.6)`;
        rimColor = `hsla(${hue}, ${sat}%, 72%, 0.4)`;
      } else if (treatment === 'TERRAN' || treatment === 'OCEANIC') {
        baseColor = `hsl(${hue}, ${sat}%, 46%)`;
        bandColor = `hsla(${hue + 8}, ${sat}%, 38%, 0.55)`;
        rimColor = `hsla(${hue}, ${sat}%, 70%, 0.5)`;
      } else { // BARREN / MOUNTAINOUS
        baseColor = `hsl(${hue}, ${Math.max(6, sat - 24)}%, 42%)`;
        bandColor = `hsla(${hue}, ${sat}%, 32%, 0.5)`;
        rimColor = `hsla(${hue}, 10%, 70%, 0.4)`;
      }
      // distance dimming: smaller/farther bodies sit fainter; brighter at night.
      const alpha = (0.4 + Math.min(9, s.sizeClass) / 9 * 0.3) * (0.55 + nightBoost * 0.45);
      skyPlanets.push({ x: px, y: py, r, treatment, hue, sat, baseColor, bandColor, rimColor, rings: s.rings, alpha });
    }
  }

  // --- RIDGE noise (the 3×48 pts) — drifted profile recomputed per frame ---
  // ampBoost: jagged for peaks, flatter for plains/flats. landBaseFrac lifts the
  // whole land mass per landform (cliff sits high, plains sit low).
  const peaky = landform === 'MTN_PEAKS' || landform === 'ICE_PEAKS' || landform === 'VOLC_CALDERA';
  const flat = landform === 'DES_SALTFLAT' || landform === 'VOLC_PLAIN' || landform === 'MTN_PLATEAU' ||
               landform === 'BAR_ROCKY' || landform === 'OCEAN_ISLAND';
  const ampBoost = pal.flourish === 'MOUNTAINOUS' ? 1.7 : peaky ? 1.6 : flat ? 0.45 : 1.0;
  const baseLift = (pal.flourish === 'MOUNTAINOUS' ? -0.06 : 0) + landBaseFrac;
  const layerCfg = [
    { base: 0.6 + baseLift, amp: 0.1 * ampBoost, speed: 1.2, seed: 5 },
    { base: 0.7 + baseLift, amp: 0.13 * ampBoost, speed: 2.6, seed: 11 },
    { base: 0.84 + baseLift, amp: 0.16 * ampBoost, speed: 4.6, seed: 23 }
  ];
  const period = Math.max(w * 2, 1200);
  const layers: RidgeLayer[] = layerCfg.map((cfg) => {
    const rng = splitmix32(worldSeed * 131 + cfg.seed);
    const pts: number[] = [];
    for (let i = 0; i < 48; i++) pts.push(rng());
    return { base: cfg.base, amp: cfg.amp, speed: cfg.speed, pts, color: '' };
  });
  const ridgeColors = [pal.ridges[0], pal.ridges[1], pal.ridges[2]];

  // --- VOLCANIC fissures (seeded; pulse per frame) ---
  const volcFissures: VolcFissure[] = [];
  if (pal.flourish === 'VOLCANIC') {
    const fRng = splitmix32(worldSeed * 313 + 41);
    const count = 2 + Math.floor(fRng() * 2);
    for (let i = 0; i < count; i++) {
      const x = w * (0.15 + fRng() * 0.7);
      const fw = w * (0.04 + fRng() * 0.06);
      volcFissures.push({ x, w: fw, phase: i * 2 });
    }
  }

  // --- FLORA layout (sway per frame; baseY from live ridge per frame) ---
  // Vegetation reads as PLANTS (palms/reeds/grass/bushes/shrubs), clustered on
  // LAND only — never floating on water. The silhouette is chosen per type.
  const flora = pal.flora.split(',').map((s) => parseInt(s.trim(), 10));
  const floraEligible = pal.flourish === 'TERRAN' || pal.flourish === 'OCEANIC' ||
    pal.flourish === 'MOUNTAINOUS' || pal.flourish === 'DESERT' ||
    (pal.flourish === 'ICE' && habN > 0.4);
  const flouraSeeds: FloraSeed[] = [];
  if (floraEligible && habN > 0.15) {
    const baseCount = pal.flourish === 'DESERT' ? 12 : pal.flourish === 'ICE' ? 8 : 30;
    // On WATER worlds the only land is the thin foreground shore — scatter a FEW
    // plants there, never across the sea. Cap hard so they cluster on the beach.
    const floraCount = hasWater
      ? Math.min(6, Math.max(2, Math.round(habN * 6)))
      : Math.round(habN * baseCount);
    const flRng = splitmix32(worldSeed * 619 + 71);
    // x-window the plants must fall within so they sit on LAND, not water.
    // For ocean ISLAND the land is the foreground centre; for SHORELINE/COAST the
    // land is one half of the scene; cliff-top plants run the full width (land is
    // the foreground plateau). Non-water worlds use the full width.
    // On water worlds the shore spans the full width (the foreground band the
    // player stands on), so plants may sit anywhere along it; their baseY comes
    // from the shoreline profile (frontProfile) at draw time. On the island the
    // near shore is centred. Non-water worlds use the full ridge width.
    let xMin = 0, xMax = w;
    if (landform === 'OCEAN_ISLAND') { xMin = w * 0.18; xMax = w * 0.82; }
    else if (landform === 'OCEAN_CLIFF' && headland) {
      // plants live on the clifftop plateau (the land side), inset from the edge.
      if (headland.side === 'left') { xMin = w * 0.04; xMax = w * (headland.landFrac - 0.06); }
      else { xMin = w * (1 - headland.landFrac + 0.06); xMax = w * 0.96; }
    }
    for (let i = 0; i < floraCount; i++) {
      const x = xMin + flRng() * (xMax - xMin);
      const wob = flRng() * 0.4 + 0.8;
      const lean = (flRng() - 0.5) * 0.5;
      // pick silhouette by type + landform
      let kind: FloraKind;
      let height: number;
      let blades = 3;
      if (pal.flourish === 'OCEANIC') {
        kind = flRng() > 0.45 ? 'PALM' : 'REED';
        height = kind === 'PALM' ? (18 + flRng() * 18) * (0.6 + habN * 0.7)
                                 : (8 + flRng() * 10) * (0.6 + habN * 0.6);
        blades = kind === 'PALM' ? 4 + Math.floor(flRng() * 3) : 3 + Math.floor(flRng() * 3);
      } else if (pal.flourish === 'DESERT') {
        kind = 'SHRUB';
        height = (5 + flRng() * 8) * (0.6 + habN * 0.6);
        blades = 3 + Math.floor(flRng() * 3);
      } else if (pal.flourish === 'MOUNTAINOUS' || pal.flourish === 'ICE') {
        kind = 'TUSSOCK';
        height = (4 + flRng() * 7) * (0.6 + habN * 0.6);
        blades = 4 + Math.floor(flRng() * 4);
      } else { // TERRAN
        kind = flRng() > 0.55 ? 'BUSH' : 'GRASS';
        height = kind === 'BUSH' ? (7 + flRng() * 10) * (0.6 + habN * 0.7)
                                 : (5 + flRng() * 9) * (0.6 + habN * 0.7);
        blades = kind === 'GRASS' ? 4 + Math.floor(flRng() * 4) : 3 + Math.floor(flRng() * 2);
      }
      flouraSeeds.push({ x, height, swayPhase: i, wob, kind, lean, blades });
    }
  }

  // --- CITADEL skyline layout (windows/beacon/heights fixed; y per frame) ---
  // On an ocean island the city must stay on the foreground land mass; on an ocean
  // CLIFF it must stay on the clifftop headland side — so towers never stand over
  // open water. Both clamp the citadel x-window to the land x-window.
  let citadelLandX = citadelOnWater ? { min: w * 0.30, max: w * 0.70 } : undefined;
  if (landform === 'OCEAN_CLIFF' && headland) {
    citadelLandX = headland.side === 'left'
      ? { min: w * 0.05, max: w * (headland.landFrac - 0.06) }
      : { min: w * (1 - headland.landFrac + 0.06), max: w * 0.95 };
  }
  const citadelLayout = citadel > 0 ? buildCitadelLayout(w, h, worldSeed, citadel, pal, citadelLandX) : null;

  // --- HAZE seeds ---
  const hazeRng = splitmix32(worldSeed * 53 + 7);
  const haze3: HazeSeed[] = [];
  for (let i = 0; i < 3; i++) {
    haze3.push({
      baseX: hazeRng() * w,
      yFrac: 0.5 + i * 0.13,
      w: w * (0.5 + hazeRng() * 0.3),
      speed: 3 + i * 2
    });
  }
  const hazeStrength = 0.04 + habN * 0.09;

  // --- DRIFTING CLOUD bands (parallax across the sky) ---
  const cloudRng = splitmix32(worldSeed * 877 + 29);
  const cloudCount = pal.flourish === 'VOLCANIC' || pal.flourish === 'NONE' ? 2 : 3;
  const clouds: CloudSeed[] = [];
  for (let i = 0; i < cloudCount; i++) {
    clouds.push({
      x: cloudRng() * w,
      yFrac: 0.1 + cloudRng() * 0.24,
      w: w * (0.35 + cloudRng() * 0.45),
      hFrac: 0.05 + cloudRng() * 0.05,
      speed: 4 + i * 5 + cloudRng() * 4, // slow parallax
      alpha: 0.05 + cloudRng() * 0.05
    });
  }
  // cloud tint derived from the haze palette (keeps it in-world)
  const cloudTint = pal.haze;

  // --- ATMOSPHERIC PARTICLES ---
  const particleKind = particleKindFor(pal.flourish, habN, hasWater);
  // count scales a little with habitability; bounded for perf.
  let pCount: number;
  if (particleKind === 'FAINT') pCount = Math.round(8 + habN * 6);
  else if (particleKind === 'EMBER') pCount = 26;
  else if (particleKind === 'SNOW') pCount = 38;
  else if (particleKind === 'DUST') pCount = 26;
  else if (particleKind === 'SPRAY') pCount = 24;
  else pCount = Math.round(8 + habN * 8); // MOTE — kept LOW/sparse
  const pRng = splitmix32(worldSeed * 401 + 13);
  const particles: ParticleSeed[] = [];
  for (let i = 0; i < pCount; i++) {
    particles.push({
      x: pRng() * w,
      y: pRng() * h,
      size: 0.6 + pRng() * 1.4,
      phase: pRng() * Math.PI * 2,
      speed: 0.4 + pRng() * 1.2,
      drift: (pRng() - 0.5) * 2,
      warm: pRng()
    });
  }

  return {
    key, ctx, horizonY, habN, citadel, prox, sc, profile,
    flourish: pal.flourish, flora, haze: pal.haze,
    tod, todBright, showSun, landform, hasWater, waterTopY, landBaseFrac, citadelOnWater,
    reflX, reflTint,
    sunX, sunY, sunR, coronaR,
    moons, moonProminence, skyPlanets, waves, weather, precipSeeds, creatures,
    shoreY, shoreColor, shoreFoamColor, shoreProfile, shoreCurve, farLands, headland,
    hasCompanion, c2x, c2y, c2r, c2,
    layers, period, ridgeColors,
    skyGrad, washGrad, coronaGrad, discGrad, companionCorona, glowGrad, starHueGlow, waterBand,
    stars, flouraSeeds, citadelLayout, haze3, hazeStrength,
    particles, particleKind, clouds, cloudTint, volcFissures,
    desertBands: pal.flourish === 'DESERT',
    iceSheen: pal.flourish === 'ICE'
  };
}

function drawLandedScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sectorId: number,
  t: number,
  pal: LandedPalette,
  env?: LandedCtx
): void {
  // habitability is bucketed (rounded to 5%) so small live jitter doesn't
  // thrash the cache; it still re-tiers count/density at the next bucket.
  const habBucket = env && typeof env.habitability === 'number'
    ? Math.round(Math.max(0, Math.min(100, env.habitability)) / 5) : 11;
  const citKey = env ? Math.max(0, Math.min(5, Math.round(env.citadelLevel || 0))) : 0;
  // Palette identity is part of the key: flourish 'NONE' maps to TWO distinct
  // palettes (gray BARREN vs violet GAS/unknown default), so flourish alone is
  // ambiguous — append the actual palette colours so a violet world never reuses
  // a gray world's cached sky/ridge/haze/flora gradients.
  const key = `${(sectorId >>> 0) || 1}|${pal.flourish}|${pal.skyTop}|${pal.ridges[0]}|${pal.haze}|${pal.flora}` +
    `|${citKey}|${habBucket}|${w}|${h}` +
    `|${env?.starKind || ''}|${env?.starColor || ''}|${env?.secondaryColor || ''}|${env?.orbitAu ?? ''}` +
    // per-world identity: landed planet id seeds time-of-day + landform, and the
    // body's moon/phase/ring/size drive the sky moons — all must bust the cache.
    `|${env?.landedPlanetId || ''}|${env?.moons ?? ''}|${env?.phaseDeg ?? ''}|${env?.rings ? 1 : 0}|${env?.sizeClass ?? ''}` +
    // sibling bodies drive the distant sky planets — bust the cache when they change.
    `|${(env?.siblings || []).map((s) => `${s.kind}:${s.sizeClass}:${s.hue}:${s.sat}:${s.rings ? 1 : 0}`).join(',')}` +
    // day bucket: sea-state weather is deterministic per (world, day) → rebuild daily.
    `|d${Math.floor(Date.now() / 86400000)}`;

  let cache = landedCache;
  // Rebuild on key change OR when the canvas context identity changes — a remount
  // yields a new ctx and the cached gradients are bound to the destroyed ctx.
  if (!cache || cache.key !== key || cache.ctx !== ctx) {
    cache = buildLandedCache(ctx, key, w, h, sectorId, pal, env);
    landedCache = cache;
  }

  const { horizonY, habN, citadel, prox, sc, profile } = cache;

  // 1) Sky gradient (cached) ---------------------------------------------------
  ctx.fillStyle = cache.skyGrad;
  ctx.fillRect(0, 0, w, h);

  // Star-tinted sky wash (cached)
  if (cache.washGrad) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = cache.washGrad;
    ctx.fillRect(0, 0, w, horizonY * 1.15);
    ctx.restore();
  }

  // 1b) WEATHER SKY — shared biome-tinted overcast overlay (storm-grey for rain,
  //     tan for sand, white for snow, smoky-red for ash). Darkens + tints by tier.
  if (cache.weather && cache.weather.skyDarken > 0) {
    drawWeatherSky(ctx, w, horizonY, cache.weather);
  }

  // 2) STARFIELD — layout cached; twinkle per frame ---------------------------
  if (cache.stars.length > 0) {
    ctx.save();
    ctx.fillStyle = '#dfe7f5';
    for (let i = 0; i < cache.stars.length; i++) {
      const s = cache.stars[i];
      const tw = t === 0 ? 0.75 : 0.5 + 0.5 * Math.sin(t * s.twSpeed + s.twPhase);
      ctx.globalAlpha = s.baseAlpha * tw;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  // 2b) DRIFTING CLOUD bands (behind the sun, slow parallax) ------------------
  if (cache.clouds.length > 0) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < cache.clouds.length; i++) {
      const c = cache.clouds[i];
      const span = w * 1.6;
      const cx = (((c.x + t * c.speed) % span) + span) % span - w * 0.3;
      const cw = c.w;
      const chh = h * c.hFrac;
      // yFrac is sky-relative; scale into the sky band, then clamp so the band's
      // bottom edge stays in open sky above the horizon (never grazes the ridge).
      const cy = Math.min(horizonY * c.yFrac * 2, horizonY - chh - 4);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, cw);
      g.addColorStop(0, `rgba(${cache.cloudTint}, ${c.alpha.toFixed(3)})`);
      g.addColorStop(1, `rgba(${cache.cloudTint}, 0)`);
      ctx.fillStyle = g;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.scale(1, chh / cw);
      ctx.translate(-cx, -cy);
      ctx.fillRect(cx - cw, cy - cw, cw * 2, cw * 2);
      ctx.restore();
    }
    ctx.restore();
  }

  // 3) THE SUN DISC (corona + disc gradients cached) --------------------------
  // GATED by time of day: a NIGHT sky never carries a sun (the reported bug).
  const { sunX, sunY, sunR, coronaR } = cache;
  if (cache.showSun) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    // storm/hurricane days dim the sun (clouds in front of it).
    const sunDim = cache.weather ? 1 - cache.weather.skyDarken * 0.8 : 1;
    // very subtle "breathing" of the corona via alpha (gradient stays cached)
    ctx.globalAlpha = (t === 0 ? 1 : 0.92 + 0.08 * Math.sin(t * 0.5)) * sunDim;
    ctx.fillStyle = cache.coronaGrad;
    ctx.fillRect(sunX - coronaR, sunY - coronaR, coronaR * 2, coronaR * 2);
    ctx.globalAlpha = sunDim;
    ctx.fillStyle = cache.discGrad;
    ctx.beginPath();
    ctx.arc(sunX, sunY, sunR, 0, Math.PI * 2);
    ctx.fill();
    if (cache.hasCompanion && cache.companionCorona) {
      const { c2, c2x, c2y, c2r } = cache;
      ctx.fillStyle = cache.companionCorona;
      ctx.fillRect(c2x - c2r * 4, c2y - c2r * 4, c2r * 8, c2r * 8);
      ctx.beginPath();
      ctx.arc(c2x, c2y, c2r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${Math.min(255, c2.r + 60)}, ${Math.min(255, c2.g + 60)}, ${Math.min(255, c2.b + 60)}, 0.95)`;
      ctx.fill();
    }
    ctx.restore();
  }

  // 3a2) SIBLING PLANETS — distant discs high in the sky (matches the system view).
  //      Drawn behind the moons (which are closer). Subtle so they never fight the
  //      HUD: small, dimmed, per-kind treatment at a distance.
  if (cache.skyPlanets.length > 0) {
    drawLandedSkyPlanets(ctx, t, cache);
  }

  // 3b) MOONS — phased discs (crescent/gibbous/full via a terminator shadow).
  if (cache.moons.length > 0) {
    drawLandedMoons(ctx, t, cache);
  }

  // 4) Horizon glow (cached gradients) ----------------------------------------
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.fillStyle = cache.glowGrad;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = cache.starHueGlow;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();

  // 4a2) DISTANT LANDMASSES for NON-WATER worlds — far mountains/mesas/dunes at the
  //      horizon, off-centre + irregular, drawn behind the foreground ridges.
  if (!cache.hasWater && cache.farLands.length > 0) {
    drawFarLands(ctx, cache.farLands, horizonY + 1);
  }

  // 4b) REAL OCEAN — depth gradient + rippling wave crests + reflection + foam.
  //     Drawn before the ridges so the foreground land mass occludes it as the
  //     near shore/cliff. Reads as MOVING WATER, not a static teal band.
  if (cache.hasWater && cache.waterBand) {
    const wt = cache.waterTopY;
    const wh = h - wt;
    ctx.save();
    // 1) base water body
    ctx.fillStyle = cache.waterBand;
    ctx.fillRect(0, wt, w, wh);

    // 1b) DISTANT LANDMASSES at the waterline — 0, 1, or 2 irregular, OFF-CENTRE
    //     silhouettes. Drawn under the waves so ripples cross in front (shared
    //     renderer; baseline = waterTopY for water worlds).
    drawFarLands(ctx, cache.farLands, wt + 1);

    // 2) wave crest-lines — clearly visible sine ripples; denser near the waterline,
    //    larger toward the foreground (parallax). Each line is drawn TWICE: a dark
    //    trough stroke just below, then a bright crest stroke on top, so the surface
    //    reads as moving water with depth, not a flat tint. Cheap per-frame math.
    // SWELL FACES — each swell is a FILLED, lit wave face that visibly HEAVES up
    // and down (vertical bob), with non-uniform crests (sum of two sines, varied
    // size) so it never reads as a repeating comb of identical stripes. A bright
    // crest highlight + whitecap foam on the nearer swells complete the moving sea.
    const crestRGB = reflWaveRGB(cache);
    const whitecapD = cache.weather ? cache.weather.whitecapDensity : 0.4;
    for (let wi = 0; wi < cache.waves.length; wi++) {
      const wv = cache.waves[wi];
      const f = wv.yFrac;
      // VERTICAL BOB — the whole swell rises & falls over time (the motion that
      // was missing). Bigger + slower toward the foreground.
      const bob = t === 0 ? 0 : Math.sin(t * wv.swellRate * 0.7 + wv.swellPhase) * (4 + f * 18);
      const baseY = wt + f * wh + bob;
      const drift = t === 0 ? 0 : t * wv.speed * 24 * wv.dir;
      const crossDrift = t === 0 ? 0 : t * 5 * wv.dir;
      const chopDrift = t === 0 ? 0 : t * (12 + f * 30) * wv.dir;
      const swell = t === 0 ? 1 : 1 + 0.4 * Math.sin(t * wv.swellRate + wv.swellPhase);
      const amp = wv.amp * swell;
      const yAt = (x: number): number =>
        baseY
        + Math.sin((x + drift) / wv.wavelength * Math.PI * 2 + wv.phase) * amp
        + Math.sin((x + crossDrift) / wv.crossWavelength * Math.PI * 2 + wv.swellPhase) * wv.crossAmp
        // weather chop — high-frequency ripple riding on the swell (0 when calm).
        + (wv.chopAmp > 0 ? Math.sin((x + chopDrift) / wv.chopWavelength * Math.PI * 2 + wv.phase * 2) * wv.chopAmp : 0);

      // FINE ripple layer: a single thin fast stroke between the big swells — no
      // filled face, no foam. Adds multi-scale richness cheaply.
      if (wv.fine) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = wv.alpha;
        ctx.strokeStyle = `rgba(${crestRGB}, 1)`;
        ctx.lineWidth = wv.lineW;
        ctx.beginPath();
        for (let x = 0; x <= w; x += 12) { const y = yAt(x); if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
        ctx.stroke();
        ctx.restore();
        continue;
      }
      // filled wave FACE: crest edge → down a perspective-scaled slab. A lit face
      // over the dark water body gives each swell real body (not a thin line).
      const slab = 6 + f * 30;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(0, yAt(0));
      for (let x = 0; x <= w; x += 10) ctx.lineTo(x, yAt(x));
      for (let x = w; x >= 0; x -= 10) ctx.lineTo(x, yAt(x) + slab);
      ctx.closePath();
      const faceGrad = ctx.createLinearGradient(0, baseY - amp, 0, baseY + slab);
      faceGrad.addColorStop(0, `rgba(${Math.round(70 + f * 60)}, ${Math.round(140 + f * 50)}, ${Math.round(175 + f * 40)}, ${(0.30 + f * 0.22).toFixed(3)})`);
      faceGrad.addColorStop(1, 'rgba(6, 26, 48, 0)');
      ctx.fillStyle = faceGrad;
      ctx.fill();
      ctx.restore();
      // bright crest highlight on the very top edge
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.28 + f * 0.45;
      ctx.strokeStyle = `rgba(${crestRGB}, 1)`;
      ctx.lineWidth = wv.lineW;
      ctx.beginPath();
      for (let x = 0; x <= w; x += 8) { const y = yAt(x); if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
      ctx.stroke();
      ctx.restore();
      // whitecap foam — bright flecks on the cresting tops of the nearer swells.
      // Weather drives how far inshore the caps reach + how densely they pop.
      const capThresh = 0.5 - whitecapD * 0.35;   // rougher → caps appear farther out
      if (f > capThresh && whitecapD > 0.02) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.fillStyle = 'rgba(235, 248, 255, 1)';
        // denser foam in rougher seas (shorter spacing between caps).
        const span = Math.max(36, wv.wavelength * (0.95 - whitecapD * 0.5));
        const popThresh = 0.75 - whitecapD * 0.35; // rougher → more caps light up
        for (let x = (wi * 53) % span; x <= w; x += span) {
          const tw = t === 0 ? 0.55 : 0.5 + 0.5 * Math.sin(t * 2.3 + x * 0.05 + wi);
          if (tw > popThresh) {
            const y = yAt(x);
            ctx.globalAlpha = Math.min(0.9, (0.3 + f * 0.4) * tw * (0.6 + whitecapD * 0.8));
            ctx.fillRect(x - 2, y - 1, 5 + f * 4, 1.8);
          }
        }
        ctx.restore();
      }
    }

    // 3) reflection glitter column under the sun (day) or brightest moon (night).
    //    A MOONLESS night has no light source — skip the column entirely so the
    //    ocean shows only ambient ripples, not a glitter under a hidden sun.
    const refl = cache.reflX;
    const hasLightSource = cache.showSun || cache.moons.length > 0;
    const reflBright = cache.showSun ? 1.0 : 0.55 * cache.moonProminence;
    if (hasLightSource) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < 14; i++) {
      const f = i / 13;
      const gy = wt + f * wh;
      // shimmering width: narrow near the waterline, fanning toward foreground.
      const baseW = (w * 0.015) * (1 + f * 3.2);
      const flick = t === 0 ? 0.75 : 0.55 + 0.45 * Math.sin(t * 2.2 + i * 1.3);
      const gwid = baseW * flick;
      ctx.globalAlpha = 0.16 * (1 - f * 0.5) * reflBright;
      ctx.fillStyle = `rgba(${cache.reflTint}, 1)`;
      // a few broken glints rather than a solid bar
      const segs = 2 + (i % 3);
      for (let s = 0; s < segs; s++) {
        const jx = (Math.sin(t * 1.7 + i * 2 + s * 3.1) * gwid * 0.6);
        ctx.fillRect(refl + jx - gwid / (segs * 2), gy, gwid / segs, 1.6);
      }
    }
    ctx.restore();
    }

    // 4) foam at the waterline — a soft bright lip where water meets the horizon/land.
    //    Heavier + thicker in rougher weather (foamMul).
    const foamMul = cache.weather ? cache.weather.foamMul : 1;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = 'rgba(220, 240, 250, 1)';
    ctx.lineWidth = 1.4 * Math.min(2.5, foamMul);
    ctx.beginPath();
    for (let x = 0; x <= w; x += 8) {
      const drift = t === 0 ? 0 : t * 18;
      const fy = wt + 1 + Math.sin((x + drift) / 40 * Math.PI * 2) * 1.6 * foamMul;
      if (x === 0) ctx.moveTo(x, fy); else ctx.lineTo(x, fy);
    }
    ctx.globalAlpha = Math.min(0.6, (0.22 + (t === 0 ? 0 : 0.06 * Math.sin(t * 2))) * foamMul);
    ctx.stroke();
    ctx.restore();

    // 5) AQUATIC LIFE — a dolphin-like back breaches at seeded intervals, leaving
    //    a ripple + splash. Reduced-motion (t=0): draw only a calm static ripple.
    if (cache.creatures.length > 0) {
      for (let ci = 0; ci < cache.creatures.length; ci++) {
        const cr = cache.creatures[ci];
        const sy = wt + cr.surfaceFrac * wh; // sea surface y at this creature
        if (t === 0) {
          // calm static ripple at the breach spot (no animation)
          ctx.save();
          ctx.globalCompositeOperation = 'lighter';
          ctx.strokeStyle = 'rgba(210, 235, 245, 0.4)';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.ellipse(cr.x, sy, cr.size * 0.9, cr.size * 0.3, 0, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
          continue;
        }
        // breach phase: 0..1 within the active window, else dormant.
        const cyc = (t + cr.offset) % cr.period;
        if (cyc > cr.dur) continue;        // dormant between breaches
        const u = cyc / cr.dur;            // 0 → emerge, 0.5 → apex, 1 → submerged
        const arc = Math.sin(u * Math.PI); // smooth up-and-down
        const backY = sy - arc * cr.size * 1.3;
        const dir = cr.dir;
        ctx.save();
        // expanding ripple ring at the breach point (grows as the creature rises)
        ctx.globalCompositeOperation = 'lighter';
        ctx.strokeStyle = 'rgba(210, 235, 245, 1)';
        ctx.globalAlpha = 0.45 * (1 - u * 0.5);
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.ellipse(cr.x, sy + 1, cr.size * (0.6 + u * 1.6), cr.size * (0.2 + u * 0.5), 0, 0, Math.PI * 2);
        ctx.stroke();
        // dark smooth back + dorsal fin arcing out of the water
        ctx.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = 0.85 * arc;
        ctx.fillStyle = 'rgba(28, 36, 48, 1)';
        ctx.beginPath();
        // back: a smooth crescent from the waterline up over the apex and back down
        ctx.moveTo(cr.x - cr.size * 0.9 * dir, sy);
        ctx.quadraticCurveTo(cr.x - cr.size * 0.2 * dir, backY, cr.x + cr.size * 0.3 * dir, backY + cr.size * 0.15);
        ctx.quadraticCurveTo(cr.x + cr.size * 0.7 * dir, backY + cr.size * 0.4, cr.x + cr.size * 0.9 * dir, sy);
        ctx.closePath();
        ctx.fill();
        // dorsal fin (a small triangle on the apex of the back)
        ctx.beginPath();
        ctx.moveTo(cr.x - cr.size * 0.05 * dir, backY + cr.size * 0.05);
        ctx.lineTo(cr.x + cr.size * 0.12 * dir, backY - cr.size * 0.35);
        ctx.lineTo(cr.x + cr.size * 0.25 * dir, backY + cr.size * 0.05);
        ctx.closePath();
        ctx.fill();
        // small splash near the apex tail-end
        if (u > 0.35 && u < 0.75) {
          ctx.globalCompositeOperation = 'lighter';
          ctx.globalAlpha = 0.5 * arc;
          ctx.fillStyle = 'rgba(225, 245, 252, 1)';
          for (let s = 0; s < 4; s++) {
            const sx = cr.x + (Math.sin(s * 2.1 + ci) * cr.size * 0.5);
            const spy = backY + Math.cos(s * 1.7) * cr.size * 0.2;
            ctx.fillRect(sx, spy, 1.4, 1.4);
          }
        }
        ctx.restore();
      }
    }
    ctx.restore();
  }

  // 5) FOREGROUND LAND.
  //   WATER worlds: a single LOW foreground shore (the strand you stand on) — NOT
  //   the 3 parallax mountain ridges, which would bury the ocean. The shore's
  //   surface line becomes frontProfile so flora + citadel sit on the shore.
  //   NON-water worlds: the existing 3-ridge mountain silhouettes, unchanged.
  let frontProfile: number[] | null = null;
  if (cache.hasWater && cache.headland) {
    // OCEAN_CLIFF — a foreground SIDE headland. One side is a solid land mass rising
    // to a clifftop plateau; the open side reveals the dominant sea. frontProfile is
    // set ONLY over the headland x-window (open side has no land → structures stay
    // off the water).
    const hl = cache.headland;
    const tp = hl.topProfile;
    const isLeft = hl.side === 'left';
    const landEdgeX = isLeft ? w * hl.landFrac : w * (1 - hl.landFrac); // the open-side cliff edge
    // sample the clifftop surface at a given x (within the land), with deterministic
    // undulation from the cached topProfile.
    const topYAt = (x: number): number => {
      // map x across the LAND width to a topProfile index
      const f = isLeft ? (x / Math.max(1, landEdgeX)) : ((x - landEdgeX) / Math.max(1, w - landEdgeX));
      const fi = Math.max(0, Math.min(1, f)) * (tp.length - 1);
      const i0 = Math.floor(fi);
      const i1 = Math.min(tp.length - 1, i0 + 1);
      const fr = fi - i0;
      const v = tp[i0] * (1 - fr) + tp[i1] * fr;
      return hl.plateauY - (v - 0.5) * h * 0.05; // gentle plateau undulation
    };

    // 1) cliff FACE — a clean curved drop from the clifftop edge down to the sea on
    //    the open side. Drawn darker; gives the headland body its mass.
    ctx.save();
    ctx.fillStyle = hl.faceColor;
    ctx.beginPath();
    if (isLeft) {
      ctx.moveTo(0, h);
      ctx.lineTo(0, topYAt(0));
      for (let x = 0; x <= landEdgeX; x += 8) ctx.lineTo(x, topYAt(x));
      // curved cliff edge dropping to the sea
      const edgeTopY = topYAt(landEdgeX);
      ctx.quadraticCurveTo(landEdgeX + w * 0.04, (edgeTopY + h) * 0.5, landEdgeX, h);
      ctx.closePath();
    } else {
      ctx.moveTo(w, h);
      ctx.lineTo(w, topYAt(w));
      for (let x = w; x >= landEdgeX; x -= 8) ctx.lineTo(x, topYAt(x));
      const edgeTopY = topYAt(landEdgeX);
      ctx.quadraticCurveTo(landEdgeX - w * 0.04, (edgeTopY + h) * 0.5, landEdgeX, h);
      ctx.closePath();
    }
    ctx.fill();
    ctx.restore();

    // 2) clifftop PLATEAU cap — a lighter band along the top surface so the player
    //    reads a flat-ish standing surface above the dark face.
    ctx.save();
    ctx.fillStyle = hl.topColor;
    ctx.beginPath();
    const capDepth = h * 0.06;
    if (isLeft) {
      ctx.moveTo(0, topYAt(0) + capDepth);
      ctx.lineTo(0, topYAt(0));
      for (let x = 0; x <= landEdgeX; x += 8) ctx.lineTo(x, topYAt(x));
      ctx.lineTo(landEdgeX, topYAt(landEdgeX) + capDepth);
      ctx.closePath();
    } else {
      ctx.moveTo(w, topYAt(w) + capDepth);
      ctx.lineTo(w, topYAt(w));
      for (let x = w; x >= landEdgeX; x -= 8) ctx.lineTo(x, topYAt(x));
      ctx.lineTo(landEdgeX, topYAt(landEdgeX) + capDepth);
      ctx.closePath();
    }
    ctx.fill();
    ctx.restore();

    // 3) highlight stroke along the clifftop edge.
    ctx.save();
    ctx.strokeStyle = hl.edgeColor;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (isLeft) {
      for (let x = 0; x <= landEdgeX; x += 8) { const y = topYAt(x); if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
    } else {
      for (let x = w; x >= landEdgeX; x -= 8) { const y = topYAt(x); if (x === w) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
    }
    ctx.stroke();
    ctx.restore();

    // 4) foam where the waves meet the BASE of the cliff (the open-side waterline
    //    against the headland). A bright lip running down the cliff edge.
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = cache.shoreFoamColor;
    ctx.lineWidth = 2.5;
    ctx.globalAlpha = 0.5 + (t === 0 ? 0 : 0.14 * Math.sin(t * 1.6));
    ctx.beginPath();
    const foamX = landEdgeX + (isLeft ? 2 : -2);
    const foamTop = topYAt(landEdgeX) + h * 0.04;
    for (let y = foamTop; y <= h; y += 8) {
      const drift = t === 0 ? 0 : Math.sin((y + t * 22) / 30 * Math.PI * 2) * 2.0;
      const fx = foamX + drift * (isLeft ? 1 : -1);
      if (y === foamTop) ctx.moveTo(fx, y); else ctx.lineTo(fx, y);
    }
    ctx.stroke();
    ctx.restore();

    // frontProfile: clifftop surface over the land x-window; the open side reports a
    // y BELOW the canvas so nothing (flora/citadel) ever lands on the open water.
    const cols: number[] = [];
    for (let x = 0; x <= w; x += 8) {
      const onLand = isLeft ? x <= landEdgeX : x >= landEdgeX;
      cols.push(onLand ? topYAt(x) : h + 9999);
    }
    frontProfile = cols;
  } else if (cache.hasWater) {
    // CURVED ORGANIC SHORE — a seeded bay / diagonal beach / cove. The sand top is
    // a smooth curve (shoreCurveYAt), filled down to the bottom; foam follows it.
    // The curve becomes frontProfile so flora + citadel sit on the sand.
    const curve = cache.shoreCurve;
    const sp = cache.shoreProfile;
    const cols: number[] = [];
    const yAtShore = (x: number): number => curve
      ? shoreCurveYAt(x, w, h, curve, sp)
      : cache.shoreY; // graceful fallback (should not happen for non-cliff water)
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let x = 0; x <= w; x += 8) {
      const y = yAtShore(x);
      ctx.lineTo(x, y);
      cols.push(y);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = cache.shoreColor;
    ctx.fill();
    frontProfile = cols;

    // foam line following the curved shore edge where the land meets the sea.
    const foamMul = cache.weather ? cache.weather.foamMul : 1;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = cache.shoreFoamColor;
    ctx.lineWidth = 2 * Math.min(2.2, foamMul);
    ctx.globalAlpha = Math.min(0.7, (0.5 + (t === 0 ? 0 : 0.12 * Math.sin(t * 1.6))) * (0.7 + foamMul * 0.3));
    ctx.beginPath();
    for (let x = 0; x <= w; x += 8) {
      const idx = Math.min(cols.length - 1, Math.round(x / 8));
      const drift = t === 0 ? 0 : Math.sin((x + t * 20) / 36 * Math.PI * 2) * 1.4 * foamMul;
      const y = cols[idx] + drift;
      if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();
  } else {
  for (let li = 0; li < cache.layers.length; li++) {
    const layer = cache.layers[li];
    const off = t * layer.speed;
    const period = cache.period;
    const n = layer.pts.length;
    const isFront = li === cache.layers.length - 1;
    const profileXs: number[] | null = isFront ? [] : null;
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let x = 0; x <= w; x += 8) {
      const u = (((x + off) % period) + period) % period;
      const fi = (u / period) * n;
      const i0 = Math.floor(fi) % n;
      const i1 = (i0 + 1) % n;
      const frac = fi - Math.floor(fi);
      const s = frac * frac * (3 - 2 * frac);
      const v = layer.pts[i0] * (1 - s) + layer.pts[i1] * s;
      const yTop = h * layer.base - v * h * layer.amp;
      ctx.lineTo(x, yTop);
      if (profileXs) profileXs.push(yTop);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = cache.ridgeColors[li];
    ctx.fill();
    if (profileXs) frontProfile = profileXs;
  }
  }
  const ridgeYAt = (x: number): number => {
    if (!frontProfile || frontProfile.length === 0) return h * 0.84;
    const idx = Math.max(0, Math.min(frontProfile.length - 1, Math.round(x / 8)));
    return frontProfile[idx];
  };

  // 5b) ICE pale sheen across the front ridge ---------------------------------
  if (cache.iceSheen) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = 'rgba(210, 235, 255, 0.18)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let x = 0; x <= w; x += 8) {
      const y = ridgeYAt(x);
      if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  // 5c) VOLCANIC lava fissures — seeds cached; pulse + animated gradient per frame
  if (cache.volcFissures.length > 0) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < cache.volcFissures.length; i++) {
      const f = cache.volcFissures[i];
      const fy = ridgeYAt(f.x) + 4;
      const pulse = t === 0 ? 0.5 : 0.5 + 0.5 * Math.sin(t * 1.8 + f.phase);
      const lg = ctx.createRadialGradient(f.x, fy, 0, f.x, fy, f.w * 2.2);
      lg.addColorStop(0, `rgba(255, 140, 40, ${(0.4 + pulse * 0.35).toFixed(3)})`);
      lg.addColorStop(0.5, 'rgba(255, 80, 20, 0.18)');
      lg.addColorStop(1, 'rgba(255, 60, 10, 0)');
      ctx.fillStyle = lg;
      ctx.fillRect(f.x - f.w * 2.2, fy - f.w, f.w * 4.4, f.w * 2.2);
      ctx.strokeStyle = `rgba(255, 200, 120, ${(0.5 + pulse * 0.4).toFixed(3)})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(f.x - f.w, fy);
      ctx.lineTo(f.x + f.w, fy + 3);
      ctx.stroke();
    }
    ctx.restore();
  }

  // 5d) DESERT dune shimmer band ----------------------------------------------
  if (cache.desertBands) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < 3; i++) {
      const dy = horizonY + 8 + i * 10 + (t === 0 ? 0 : Math.sin(t * 0.8 + i) * 2);
      ctx.fillStyle = `rgba(255, 200, 110, ${(0.05 - i * 0.012).toFixed(3)})`;
      ctx.fillRect(0, dy, w, 3);
    }
    ctx.restore();
  }

  // 6) FLORA — recognizable PLANTS, land-only; layout cached, sway per frame.
  if (cache.flouraSeeds.length > 0) {
    const fl = cache.flora;
    const stroke = `rgb(${fl[0]}, ${fl[1]}, ${fl[2]})`;
    const fillSoft = `rgba(${fl[0]}, ${fl[1]}, ${fl[2]}, 0.55)`;
    ctx.save();
    ctx.globalAlpha = 0.45 + habN * 0.4;
    for (let i = 0; i < cache.flouraSeeds.length; i++) {
      const f = cache.flouraSeeds[i];
      const baseY = ridgeYAt(f.x);
      const sway = (t === 0 ? 0 : Math.sin(t * 0.9 + f.swayPhase)) * (1.0 + habN * 1.2);
      drawPlant(ctx, f, baseY, sway, stroke, fillSoft);
    }
    ctx.restore();
  }

  // 7) CITADEL SKYLINE — layout cached; baseline + lights per frame -----------
  if (citadel > 0 && cache.citadelLayout) {
    drawCitadelSkyline(ctx, h, t, cache.citadelLayout, ridgeYAt);
  }

  // 8) ATMOSPHERIC PARTICLES — kind-specific living motion --------------------
  if (cache.particles.length > 0 && t !== 0) {
    drawLandedParticles(ctx, w, h, t, cache);
  } else if (cache.particles.length > 0) {
    // reduced-motion / calm static frame: draw particles at rest (no motion)
    drawLandedParticles(ctx, w, h, 0, cache);
  }

  // 9) Atmospheric haze — seeds cached; drift + animated radial per frame.
  //    Weather thickens it (hazeMul) and tints it toward the weather haze colour.
  const hazeMul = cache.weather ? cache.weather.hazeMul : 1;
  const hazeTint = cache.weather && cache.weather.skyDarken > 0 ? cache.weather.hazeColor : cache.haze;
  const hazeStr = Math.min(0.5, cache.hazeStrength * hazeMul);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < cache.haze3.length; i++) {
    const hz = cache.haze3[i];
    const hy = h * hz.yFrac + (t === 0 ? 0 : Math.sin(t * 0.05 + i * 2.1) * 4);
    const hx = ((hz.baseX + t * hz.speed) % (w * 1.4)) - w * 0.2;
    const grad = ctx.createRadialGradient(hx, hy, 0, hx, hy, hz.w);
    grad.addColorStop(0, `rgba(${hazeTint}, ${hazeStr.toFixed(3)})`);
    grad.addColorStop(1, `rgba(${hazeTint}, 0)`);
    ctx.fillStyle = grad;
    ctx.save();
    ctx.translate(hx, hy);
    ctx.scale(1, 0.22);
    ctx.translate(-hx, -hy);
    ctx.fillRect(hx - hz.w, hy - hz.w, hz.w * 2, hz.w * 2);
    ctx.restore();
  }
  ctx.restore();

  // 10) WEATHER PRECIPITATION — shared renderer: rain streaks / snow flakes / ash
  //     motes / sand streaks / dust haze, per the biome's weather. Drawn over the
  //     whole scene. Reduced-motion (t=0): static positions, no fall/drift.
  if (cache.weather && cache.precipSeeds.length > 0 && cache.weather.precip !== 'none') {
    drawPrecipitation(ctx, w, h, t, cache.weather, cache.precipSeeds);
  }

  // 11) LIGHTNING — TERRAN/JUNGLE thunderstorms: a brief full-scene flash every few
  //     seconds at STORM+. Deterministic timing; reduced-motion (t=0): no flash.
  if (cache.weather && cache.weather.lightning && t !== 0) {
    // a short bright pulse: fire ~every 4.5s, lasting ~0.18s, with a quick decay.
    const cyc = t % 4.5;
    if (cyc < 0.22) {
      const fl = Math.max(0, 1 - cyc / 0.22);
      const strike = fl * fl;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = `rgba(200, 215, 245, ${(0.5 * strike).toFixed(3)})`;
      ctx.fillRect(0, 0, w, h);
      ctx.restore();
    }
  }
}

/** Shared WEATHER SKY overlay — darkens + biome-tints the sky by tier. The tint
 *  comes from WeatherFx.hazeColor (storm-grey / tan / white / smoky-red). */
function drawWeatherSky(ctx: CanvasRenderingContext2D, w: number, horizonY: number, wx: WeatherFx): void {
  const sd = wx.skyDarken;
  const [r, g, b] = wx.hazeColor.split(',').map((s) => parseInt(s.trim(), 10));
  ctx.save();
  // darken (multiply-ish via a dark overlay), heaviest at the top
  const dark = ctx.createLinearGradient(0, 0, 0, horizonY * 1.1);
  dark.addColorStop(0, `rgba(18, 22, 30, ${(sd * 0.8).toFixed(3)})`);
  dark.addColorStop(1, `rgba(28, 34, 44, ${(sd * 0.4).toFixed(3)})`);
  ctx.fillStyle = dark;
  ctx.fillRect(0, 0, w, horizonY * 1.1);
  // biome tint wash (tan/white/red) so the overcast reads as that world's weather
  const tint = ctx.createLinearGradient(0, 0, 0, horizonY * 1.1);
  tint.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(sd * 0.22).toFixed(3)})`);
  tint.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${(sd * 0.10).toFixed(3)})`);
  ctx.fillStyle = tint;
  ctx.fillRect(0, 0, w, horizonY * 1.1);
  ctx.restore();
}

/** Shared DISTANT-LANDMASS renderer — off-centre, irregular, asymmetric silhouettes
 *  at a baseline y. Used by both water (waterline) and non-water (horizon) worlds. */
function drawFarLands(
  ctx: CanvasRenderingContext2D,
  farLands: LandedCache['farLands'],
  baseY: number
): void {
  for (let li = 0; li < farLands.length; li++) {
    const fl = farLands[li];
    const n = fl.pts.length;
    const ix0 = fl.cx - fl.halfW, ix1 = fl.cx + fl.halfW;
    const peakAt = 0.3 + fl.pts[0] * 0.4;   // skewed off-centre peak → asymmetric
    ctx.save();
    ctx.fillStyle = fl.color;
    ctx.beginPath();
    ctx.moveTo(ix0, baseY);
    for (let s = 0; s <= n - 1; s++) {
      const u = s / (n - 1);
      const x = ix0 + (ix1 - ix0) * u;
      const env = u < peakAt ? (u / peakAt) : (1 - (u - peakAt) / (1 - peakAt));
      const shaped = Math.pow(Math.max(0, env), 0.8);
      const y = baseY - shaped * fl.peak * (0.45 + fl.pts[s] * 0.55);
      ctx.lineTo(x, y);
    }
    ctx.lineTo(ix1, baseY);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
}

/** Shared PRECIPITATION renderer — rain / snow / ash / sand / dust, driven by the
 *  weather factor set. Streak/flake positions come from the cache; only sine drift
 *  + fall animate per frame. Reduced-motion (t=0): static positions, no motion. */
function drawPrecipitation(
  ctx: CanvasRenderingContext2D,
  w: number, h: number, t: number,
  wx: WeatherFx,
  seeds: LandedCache['precipSeeds']
): void {
  const span = w * 1.4;
  const intensity = wx.precipIntensity;
  ctx.save();
  if (wx.precip === 'rain' || wx.precip === 'sand') {
    // diagonal/near-horizontal STREAKS. sand is faster + flatter + warm-toned.
    const isSand = wx.precip === 'sand';
    // sand blows near-horizontal regardless of precipAngle; rain follows the wind.
    const ang = isSand ? Math.PI * 0.42 : wx.precipAngle;
    const dx = Math.sin(ang), dy = Math.cos(ang);
    const fallSpeed = (isSand ? 360 : 260) * (0.7 + wx.windMul * 0.4);
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = isSand ? 'rgba(225, 180, 110, 1)' : 'rgba(200, 220, 240, 1)';
    ctx.lineWidth = 1;
    for (let i = 0; i < seeds.length; i++) {
      const s = seeds[i];
      const baseX = s.x * span;
      const baseY = s.y * h;
      const sx = t === 0 ? baseX - w * 0.2
        : (((baseX + t * fallSpeed * s.speed * dx) % span) + span) % span - w * 0.2;
      const sy = t === 0 ? baseY : ((baseY + t * fallSpeed * s.speed * (isSand ? 0.4 : 1)) % h);
      const len = s.len * (0.8 + intensity * 0.6) * (isSand ? 1.4 : 1);
      ctx.globalAlpha = s.alpha * (0.6 + intensity * 0.6);
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(sx + dx * len, sy + dy * len);
      ctx.stroke();
    }
  } else {
    // FALLING MOTES — snow (white), ash (grey), dust (haze-tan). Drift sideways.
    const col = wx.precip === 'snow' ? '235, 245, 255'
      : wx.precip === 'ash' ? '120, 115, 110'
      : '170, 155, 135'; // dust
    const fallSpeed = (wx.precip === 'snow' ? 36 : wx.precip === 'ash' ? 22 : 16)
      * (0.7 + wx.windMul * 0.4);
    const swayMul = wx.windMul;
    ctx.globalCompositeOperation = wx.precip === 'ash' ? 'source-over' : 'lighter';
    for (let i = 0; i < seeds.length; i++) {
      const s = seeds[i];
      const baseX = s.x * w;
      const sway = t === 0 ? 0 : Math.sin(t * 0.6 + i) * 10 * swayMul + t * (2 + s.speed * 4) * swayMul;
      const x = (((baseX + sway) % w) + w) % w;
      const y = t === 0 ? s.y * h : ((s.y * h + t * fallSpeed * s.speed) % h);
      ctx.globalAlpha = s.alpha * (0.6 + intensity * 0.5);
      ctx.fillStyle = `rgba(${col}, 1)`;
      ctx.beginPath();
      ctx.arc(x, y, s.size * (wx.precip === 'dust' ? 1.4 : 0.9), 0, Math.PI * 2);
      ctx.fill();
    }
    // VOLCANIC ash-storm: a few additive glowing ember sparks riding the ash.
    if (wx.precip === 'ash') {
      ctx.globalCompositeOperation = 'lighter';
      for (let i = 0; i < seeds.length; i += 4) {
        const s = seeds[i];
        const x = (((s.x * w + (t === 0 ? 0 : t * (6 + s.speed * 10) * swayMul)) % w) + w) % w;
        const y = t === 0 ? s.y * h : ((s.y * h + t * (fallSpeed * 0.6) * s.speed) % h);
        const flick = t === 0 ? 0.6 : 0.4 + 0.6 * Math.abs(Math.sin(t * 3 + i));
        ctx.globalAlpha = 0.5 * intensity * flick;
        ctx.fillStyle = i % 8 === 0 ? 'rgba(255, 150, 60, 1)' : 'rgba(255, 100, 40, 1)';
        ctx.fillRect(x, y, s.size, s.size);
      }
    }
  }
  ctx.restore();
}

/** Wave-crest tint for the ocean — sun colour by day, pale moon tint at night. */
function reflWaveRGB(cache: LandedCache): string {
  if (cache.showSun) {
    const { r, g, b } = cache.sc;
    return `${Math.min(255, r + 30)}, ${Math.min(255, g + 50)}, ${Math.min(255, b + 60)}`;
  }
  return '150, 185, 210';
}

/** Draw the SIBLING PLANETS as distant discs in the sky — a small, dimmed echo of
 *  the flight scene's per-kind treatment (gas-giant banding, ice pale, volcanic
 *  ember, rings). Position/colours are cached; only a faint cosmetic shimmer of the
 *  rim varies per frame. Subtle by design so it never overpowers the HUD. */
function drawLandedSkyPlanets(ctx: CanvasRenderingContext2D, t: number, cache: LandedCache): void {
  for (let i = 0; i < cache.skyPlanets.length; i++) {
    const p = cache.skyPlanets[i];
    ctx.save();
    ctx.globalAlpha = p.alpha;
    // body disc (clipped) — base fill + a couple of cheap "band" arcs for character
    ctx.save();
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = p.baseColor;
    ctx.fillRect(p.x - p.r, p.y - p.r, p.r * 2, p.r * 2);
    // distance treatment: horizontal banding (gas/desert/terran) or a soft hemi
    // gradient feel via two offset bands. Cheap, deterministic, no per-frame seed.
    ctx.fillStyle = p.bandColor;
    if (p.treatment === 'GAS_GIANT') {
      for (let b = -2; b <= 2; b++) {
        ctx.fillRect(p.x - p.r, p.y + b * p.r * 0.4 - p.r * 0.12, p.r * 2, p.r * 0.24);
      }
    } else if (p.treatment === 'VOLCANIC') {
      // ember mottling — a few additive warm blots
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = p.bandColor;
      ctx.fillRect(p.x - p.r * 0.6, p.y - p.r * 0.2, p.r * 0.5, p.r * 0.4);
      ctx.fillRect(p.x + p.r * 0.1, p.y + p.r * 0.1, p.r * 0.4, p.r * 0.3);
      ctx.restore();
    } else if (p.treatment !== 'BARREN' && p.treatment !== 'MOUNTAINOUS') {
      ctx.fillRect(p.x - p.r, p.y - p.r * 0.1, p.r * 2, p.r * 0.5);
    }
    // shaded limb: darken the lower-right for a lit-sphere read
    const lg = ctx.createRadialGradient(p.x - p.r * 0.3, p.y - p.r * 0.3, p.r * 0.1, p.x, p.y, p.r * 1.2);
    lg.addColorStop(0, 'rgba(255,255,255,0.12)');
    lg.addColorStop(0.6, 'rgba(0,0,0,0)');
    lg.addColorStop(1, 'rgba(0,0,0,0.4)');
    ctx.fillStyle = lg;
    ctx.fillRect(p.x - p.r, p.y - p.r, p.r * 2, p.r * 2);
    ctx.restore();
    // faint rim shimmer (cosmetic; calm at t=0)
    const shimmer = t === 0 ? 0.5 : 0.4 + 0.2 * Math.sin(t * 0.4 + i);
    ctx.strokeStyle = p.rimColor;
    ctx.globalAlpha = p.alpha * shimmer;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r + 0.5, 0, Math.PI * 2);
    ctx.stroke();
    // rings (thin ellipse) if the body has them
    if (p.rings) {
      ctx.globalAlpha = p.alpha * 0.7;
      ctx.strokeStyle = p.rimColor;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(p.x, p.y, p.r * 1.8, p.r * 0.5, -0.3, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }
}

/** Draw the MOONS hanging in the sky, each with a terminator shadow producing a
 *  crescent/gibbous/full phase. Prominent at night, faint pale by day (scaled by
 *  cache.moonProminence). The lit body is drawn, then an unlit cap is composited
 *  over it offset along lightAngle to carve the phase. */
function drawLandedMoons(ctx: CanvasRenderingContext2D, t: number, cache: LandedCache): void {
  const prom = cache.moonProminence;
  for (let i = 0; i < cache.moons.length; i++) {
    const m = cache.moons[i];
    const breathe = t === 0 ? 1 : 0.96 + 0.04 * Math.sin(t * 0.3 + i);
    ctx.save();
    // soft halo (additive) — gradient is cached; stronger at night via its alpha.
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = m.halo;
    ctx.fillRect(m.x - m.r * 2.6, m.y - m.r * 2.6, m.r * 5.2, m.r * 5.2);
    ctx.restore();

    // lit disc
    ctx.save();
    ctx.globalAlpha = (0.5 + 0.5 * prom) * breathe;
    ctx.fillStyle = `rgb(${m.tint})`;
    ctx.beginPath();
    ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2);
    ctx.fill();
    // faint mare mottling for texture (cheap, two darker blobs; tint precomputed)
    ctx.globalAlpha *= 0.4;
    ctx.fillStyle = `rgba(${m.mareTint}, 1)`;
    ctx.beginPath();
    ctx.arc(m.x - m.r * 0.3, m.y - m.r * 0.2, m.r * 0.28, 0, Math.PI * 2);
    ctx.arc(m.x + m.r * 0.25, m.y + m.r * 0.3, m.r * 0.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // terminator: carve the unlit portion with a shadow offset along lightAngle.
    // illum 1 = full (no shadow); 0.5 = half; near 0 = thin crescent.
    if (m.illum < 0.985) {
      ctx.save();
      // clip to the moon disc, then paint the shadow region as a dark overlay.
      ctx.beginPath();
      ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2);
      ctx.clip();
      // offset of the shadow circle along the (opposite of) light direction.
      // when illum>0.5 (gibbous) the shadow is a thin sliver pushed far off-disc;
      // when illum<0.5 (crescent) the shadow covers most of the disc.
      const k = (m.illum - 0.5) * 2; // -1 (new) … +1 (full)
      const dx = -Math.cos(m.lightAngle) * m.r * k * 1.0;
      const dy = -Math.sin(m.lightAngle) * m.r * k * 1.0;
      // shadow disc radius slightly larger so its curved edge is the terminator.
      const sr = m.r * (1.0 + (1 - Math.abs(k)) * 0.04);
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = 'rgba(8, 10, 18, 0.82)';
      ctx.beginPath();
      ctx.arc(m.x + dx, m.y + dy, sr, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    // optional ring on the largest moon (when the world has rings)
    if (m.ring) {
      ctx.save();
      ctx.globalAlpha = 0.45 * prom + 0.15;
      ctx.strokeStyle = `rgba(${m.tint}, 0.8)`;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(m.x, m.y, m.r * 1.9, m.r * 0.5, -0.35, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }
}

/** Draw a single recognizable PLANT silhouette. All kinds spring from baseY (the
 *  live land surface) so nothing floats; trunks lean/sway with `sway`. */
function drawPlant(
  ctx: CanvasRenderingContext2D,
  f: FloraSeed,
  baseY: number,
  sway: number,
  stroke: string,
  fillSoft: string
): void {
  const x = f.x;
  const h = f.height;
  const tipX = x + sway + f.lean * h * 0.4;
  const tipY = baseY - h;
  ctx.strokeStyle = stroke;
  ctx.fillStyle = fillSoft;

  if (f.kind === 'PALM') {
    // curved trunk + a fan of drooping fronds at the crown
    ctx.lineWidth = 1.6 * f.wob;
    ctx.beginPath();
    ctx.moveTo(x, baseY);
    ctx.quadraticCurveTo(x + (tipX - x) * 0.4, baseY - h * 0.55, tipX, tipY);
    ctx.stroke();
    ctx.lineWidth = 1.0 * f.wob;
    for (let b = 0; b < f.blades; b++) {
      const a = (-Math.PI * 0.5) + (b / Math.max(1, f.blades - 1) - 0.5) * Math.PI * 1.1;
      const fl = h * 0.55;
      const ex = tipX + Math.cos(a) * fl;
      const ey = tipY + Math.sin(a) * fl * 0.7 + fl * 0.18; // droop down
      ctx.beginPath();
      ctx.moveTo(tipX, tipY);
      ctx.quadraticCurveTo(tipX + Math.cos(a) * fl * 0.5, tipY + Math.sin(a) * fl * 0.3, ex, ey);
      ctx.stroke();
    }
  } else if (f.kind === 'REED') {
    // a clustered tuft of thin reeds fanning from one root
    ctx.lineWidth = 1.0 * f.wob;
    for (let b = 0; b < f.blades; b++) {
      const spread = (b / Math.max(1, f.blades - 1) - 0.5);
      const bx = x + spread * h * 0.25;
      const bTipX = bx + sway * (0.6 + Math.abs(spread)) + spread * h * 0.5;
      const bTipY = baseY - h * (0.7 + Math.abs(spread) * 0.3);
      ctx.beginPath();
      ctx.moveTo(bx, baseY);
      ctx.quadraticCurveTo((bx + bTipX) / 2, baseY - h * 0.5, bTipX, bTipY);
      ctx.stroke();
    }
  } else if (f.kind === 'GRASS') {
    // a clump of curved grass blades
    ctx.lineWidth = 0.9 * f.wob;
    for (let b = 0; b < f.blades; b++) {
      const spread = (b / Math.max(1, f.blades - 1) - 0.5);
      const bx = x + spread * h * 0.4;
      const bTipX = bx + sway * (0.8 + Math.abs(spread)) + spread * h * 0.6;
      ctx.beginPath();
      ctx.moveTo(bx, baseY);
      ctx.quadraticCurveTo((bx + bTipX) / 2, baseY - h * 0.6, bTipX, baseY - h * (0.6 + Math.abs(spread) * 0.4));
      ctx.stroke();
    }
  } else if (f.kind === 'BUSH') {
    // a rounded leafy mound on a short stem
    ctx.lineWidth = 1.3 * f.wob;
    ctx.beginPath();
    ctx.moveTo(x, baseY);
    ctx.lineTo(x + sway * 0.3, baseY - h * 0.4);
    ctx.stroke();
    const cy = baseY - h * 0.65;
    ctx.beginPath();
    ctx.arc(x + sway * 0.4, cy, h * 0.45, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x + sway * 0.4 - h * 0.3, cy + h * 0.12, h * 0.3, 0, Math.PI * 2);
    ctx.arc(x + sway * 0.4 + h * 0.3, cy + h * 0.1, h * 0.32, 0, Math.PI * 2);
    ctx.fill();
  } else if (f.kind === 'SHRUB') {
    // hardy desert shrub: a few stiff angled branches from a base
    ctx.lineWidth = 1.2 * f.wob;
    for (let b = 0; b < f.blades; b++) {
      const a = (-Math.PI * 0.5) + (b / Math.max(1, f.blades - 1) - 0.5) * 1.6;
      const ex = x + Math.cos(a) * h * 0.9 + sway * 0.4;
      const ey = baseY + Math.sin(a) * h * 0.9;
      ctx.beginPath();
      ctx.moveTo(x, baseY);
      ctx.lineTo(ex, ey);
      ctx.stroke();
    }
  } else { // TUSSOCK — a low spiky hardy clump
    ctx.lineWidth = 0.9 * f.wob;
    for (let b = 0; b < f.blades; b++) {
      const a = (-Math.PI * 0.5) + (b / Math.max(1, f.blades - 1) - 0.5) * 1.3;
      const ex = x + Math.cos(a) * h * 0.6 + sway * 0.3;
      const ey = baseY + Math.sin(a) * h * 0.85;
      ctx.beginPath();
      ctx.moveTo(x, baseY);
      ctx.lineTo(ex, ey);
      ctx.stroke();
    }
  }
}

/** Atmospheric particles — planet-type-aware living motion. Positions animate
 *  from the cached seeds by t (wrap-around); never allocates. t=0 → calm rest. */
function drawLandedParticles(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  cache: LandedCache
): void {
  const kind = cache.particleKind;
  const horizonY = cache.horizonY;
  // weather intensifies the per-biome ambient particles (e.g. a volcanic ember-
  // storm whips up the existing embers; a blizzard thickens the snow).
  const pMul = cache.weather ? cache.weather.particleMul : 1;
  ctx.save();
  if (kind === 'EMBER') {
    // embers ONLY hug the lava fissures: anchored near a fissure x, short rise,
    // fire-orange. (If no fissures, fall back to spread across the surface band.)
    ctx.globalCompositeOperation = 'lighter';
    const fiss = cache.volcFissures;
    const RISE = h * 0.22; // short rise — embers stay low near the fire
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const anchorX = fiss.length > 0 ? fiss[i % fiss.length].x : (p.x % w);
      const x = anchorX + Math.sin(t * 1.1 + p.phase) * 7 + p.drift * 3;
      const rise = ((p.y % RISE) + t * (10 + p.speed * 16) * (0.7 + pMul * 0.3)) % RISE;
      const y = (cache.horizonY + RISE) - rise;
      const flick = 0.4 + 0.6 * Math.abs(Math.sin(t * 3 + p.phase));
      ctx.globalAlpha = Math.min(1, 0.55 * flick * (1 - rise / RISE) * pMul);
      ctx.fillStyle = p.warm > 0.5 ? 'rgba(255, 170, 70, 1)' : 'rgba(255, 100, 35, 1)';
      ctx.fillRect(x, y, p.size, p.size);
    }
  } else if (kind === 'SNOW') {
    // snow falling — drift + sway, cool white.
    ctx.fillStyle = 'rgba(225, 240, 255, 1)';
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const fall = (p.y + t * (10 + p.speed * 14) * (0.7 + pMul * 0.3)) % h;
      const x = (((p.x + Math.sin(t * 0.6 + p.phase) * 12 + p.drift * t * 2) % w) + w) % w;
      ctx.globalAlpha = Math.min(1, (0.3 + p.warm * 0.4) * pMul);
      ctx.beginPath();
      ctx.arc(x, fall, p.size * 0.7, 0, Math.PI * 2);
      ctx.fill();
    }
  } else if (kind === 'DUST') {
    // dust/sand blowing horizontally near the ground.
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + t * (30 + p.speed * 40) * (0.7 + pMul * 0.3)) % (w * 1.2)) + w * 1.2) % (w * 1.2) - w * 0.1;
      const y = horizonY + 6 + ((p.y + Math.sin(t * 0.7 + p.phase) * 4) % Math.max(1, h - horizonY - 6));
      ctx.globalAlpha = Math.min(1, (0.12 + p.warm * 0.12) * pMul);
      ctx.fillStyle = 'rgba(230, 190, 120, 1)';
      ctx.fillRect(x, y, p.size * 1.6, p.size * 0.7);
    }
  } else if (kind === 'SPRAY') {
    // sea spray / mist hugging the WATERLINE — low, soft, drifting sideways.
    // Plus an occasional far bird gliding across the sky.
    ctx.globalCompositeOperation = 'lighter';
    const waterTop = cache.waterTopY;
    const bandH = Math.max(8, h - waterTop);
    // weather drives spray velocity + intensity (more, faster mist in rough seas).
    const sprayMul = cache.weather ? cache.weather.spraySpeedMul : 1;
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      // most particles are spray near the waterline; ~1 in 12 is a far seagull.
      // gulls keep to the sky in fair weather only — they shelter in storms.
      const isBird = (i % 12) === 0 && (!cache.weather || cache.weather.skyDarken < 0.3);
      if (isBird) {
        // glide across the sky on a gentle ARC (not a flat horizontal line): a slow
        // sine bob superimposed on the horizontal travel. Dark seagull "M" of two
        // shallow wing-arcs whose angle flaps subtly with t.
        const span = w * 1.3;
        const bx = (((p.x + t * (12 + p.speed * 8)) % span) + span) % span - w * 0.15;
        const arc = Math.sin((bx / w) * Math.PI * 1.4 + p.phase) * cache.horizonY * 0.10;
        const by = cache.horizonY * (0.30 + p.warm * 0.22) + arc;
        const dir = p.warm > 0.5 ? 1 : -1; // facing
        const wing = 3.5 + p.size * 1.2;   // half wing-span (distant → small)
        // flap: wing-tips rise/fall; the dip at the body deepens as wings raise.
        const flap = Math.sin(t * 5 + p.phase);
        const tipY = by - flap * wing * 0.5;       // wing tips
        const bodyDip = by + (0.5 + flap * 0.4) * wing * 0.35; // shallow centre dip
        ctx.save();
        ctx.globalCompositeOperation = 'source-over'; // dark against the sky
        ctx.globalAlpha = 0.28 + 0.08 * p.warm;
        ctx.strokeStyle = 'rgba(30, 38, 50, 1)';
        ctx.lineWidth = 1.3;
        ctx.lineCap = 'round';
        ctx.beginPath();
        // left wing arc up to the body dip, then right wing arc — an "M"/seagull.
        ctx.moveTo(bx - wing * dir, tipY);
        ctx.quadraticCurveTo(bx - wing * 0.4 * dir, bodyDip - wing * 0.2, bx, bodyDip);
        ctx.quadraticCurveTo(bx + wing * 0.4 * dir, bodyDip - wing * 0.2, bx + wing * dir, tipY);
        ctx.stroke();
        ctx.restore();
        continue;
      }
      // spray: confined to a thin band just below the waterline, low rise + fade.
      // Rougher weather → faster rise + taller spray + stronger alpha.
      const riseBand = bandH * (0.4 + (sprayMul - 1) * 0.12);
      const rise = ((p.y % riseBand) + t * (6 + p.speed * 8) * sprayMul) % riseBand;
      const y = waterTop + 2 + riseBand - rise;
      const x = (((p.x + Math.sin(t * 0.5 + p.phase) * 10 + t * (3 + p.drift * 4) * sprayMul) % w) + w) % w;
      ctx.globalAlpha = (0.10 + (sprayMul - 1) * 0.04) * (1 - rise / riseBand);
      ctx.fillStyle = 'rgba(210, 235, 245, 1)';
      ctx.beginPath();
      ctx.arc(x, y, p.size * 0.9, 0, Math.PI * 2);
      ctx.fill();
    }
  } else if (kind === 'MOTE') {
    // a FEW low ground-level motes drifting just above the surface (lush worlds).
    ctx.globalCompositeOperation = 'lighter';
    const top = cache.horizonY;
    const band = Math.max(8, h - top);
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + Math.sin(t * 0.3 + p.phase) * 12 + t * (2 + p.speed * 2)) % w) + w) % w;
      // keep them LOW — within the bottom third of the surface band.
      const y = top + band * 0.55 + ((p.y + Math.sin(t * 0.4 + p.phase) * 8) % (band * 0.45));
      const pulse = 0.3 + 0.7 * Math.abs(Math.sin(t * 0.8 + p.phase));
      ctx.globalAlpha = 0.22 * pulse;
      ctx.fillStyle = p.warm > 0.5 ? 'rgba(190, 255, 170, 1)' : 'rgba(170, 220, 255, 1)';
      ctx.beginPath();
      ctx.arc(x, y, p.size * 0.7, 0, Math.PI * 2);
      ctx.fill();
    }
  } else {
    // FAINT — sparse faint dust drifting LOW near the surface (never high sky).
    const top = cache.horizonY;
    const band = Math.max(8, h - top);
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + t * (3 + p.speed * 4)) % w) + w) % w;
      const y = top + ((p.y + Math.sin(t * 0.3 + p.phase) * 6) % band);
      ctx.globalAlpha = 0.05 + p.warm * 0.06;
      ctx.fillStyle = '#cfd8e6';
      ctx.fillRect(x, y, p.size, p.size);
    }
  }
  ctx.restore();
}

/** Build the deterministic citadel-skyline layout ONCE (structures, windows,
 *  beacon, colour). Per-frame draw recomputes only baseline Y (from the live
 *  drifted ridge) + light twinkle/pulse. Density/height/lights scale with the
 *  citadel level (1 Outpost → 5 Capital). */
function buildCitadelLayout(
  w: number,
  h: number,
  seed: number,
  level: number,
  pal: LandedPalette,
  /** Optional land x-window [min,max]: when the world is an ocean island the city
   *  must sit on the small land mass, never spilling over open water. Defaults to
   *  the full width. */
  landX?: { min: number; max: number }
): CitadelLayout | null {
  const ridgeRgb = hexToRgb(pal.ridges[2]);
  const struct = `rgb(${Math.min(255, ridgeRgb.r + 34)}, ${Math.min(255, ridgeRgb.g + 38)}, ${Math.min(255, ridgeRgb.b + 46)})`;
  const rng = splitmix32(seed * 1009 + level * 17);
  // land bounds the whole skyline (cityX, spread, every structure x, the beacon).
  const lMin = landX ? Math.max(8, landX.min) : 8;
  const lMax = landX ? Math.min(w - 8, landX.max) : w - 8;
  const clampX = (x: number) => Math.max(lMin, Math.min(lMax, x));

  const cfg = [
    { n: 0, maxH: 0,    win: 0,    beacon: false },
    { n: 1, maxH: 0.05, win: 0,    beacon: false },
    { n: 3, maxH: 0.07, win: 0.1,  beacon: false },
    { n: 5, maxH: 0.11, win: 0.25, beacon: false },
    { n: 8, maxH: 0.14, win: 0.45, beacon: false },
    { n: 12, maxH: 0.2, win: 0.7,  beacon: true  }
  ][level];
  if (!cfg || cfg.n === 0) return null;

  // city centre within the land window; spread bounded so structures stay on land.
  const cityX = clampX(lMin + (lMax - lMin) * (0.3 + rng() * 0.4));
  const spread = Math.min(w * (0.12 + level * 0.05), (lMax - lMin) * 0.5);

  const structures: CitadelStructure[] = [];
  for (let i = 0; i < cfg.n; i++) {
    const jitter = (rng() - 0.5) * 2 * spread;
    const sx = clampX(cityX + jitter);
    const bw = Math.max(4, w * (0.012 + rng() * 0.02));
    const bh = h * cfg.maxH * (0.35 + rng() * 0.65);

    const windows: { dx: number; dy: number; warm: number }[] = [];
    if (cfg.win > 0) {
      const rows = Math.max(1, Math.floor(bh / 7));
      for (let r = 0; r < rows; r++) {
        for (let c = -1; c <= 1; c++) {
          if (rng() > cfg.win) continue;
          // store window offsets relative to (sx, topY) so the per-frame draw
          // can re-anchor them to the live baseline cheaply.
          windows.push({ dx: c * (bw * 0.3), dy: 4 + r * 7, warm: rng() });
        }
      }
    }

    structures.push({
      x: sx, bw, bh, windows,
      isOutpost: level === 1,
      isSpire: i === 0 && level >= 3
    });
  }

  return { structures, beacon: cfg.beacon, cityX, maxH: cfg.maxH, struct, twinkleWindows: level >= 4 };
}

/** Per-frame citadel draw — re-anchors the cached layout to the live ridge and
 *  applies window twinkle / beacon pulse. No allocations beyond the beacon gradient. */
function drawCitadelSkyline(
  ctx: CanvasRenderingContext2D,
  h: number,
  t: number,
  layout: CitadelLayout,
  ridgeYAt: (x: number) => number
): void {
  ctx.save();
  // bodies + structural flourishes
  let winIndex = 0;
  for (let s = 0; s < layout.structures.length; s++) {
    const st = layout.structures[s];
    const groundY = ridgeYAt(st.x);
    const topY = groundY - st.bh;
    ctx.fillStyle = layout.struct;
    ctx.fillRect(st.x - st.bw / 2, topY, st.bw, st.bh);
    if (st.isOutpost) {
      ctx.beginPath();
      ctx.arc(st.x, groundY - st.bh * 0.5, st.bw * 0.7, Math.PI, 0);
      ctx.fill();
      ctx.strokeStyle = layout.struct;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(st.x, topY);
      ctx.lineTo(st.x, topY - st.bh * 0.6);
      ctx.stroke();
    } else if (st.isSpire) {
      ctx.beginPath();
      ctx.moveTo(st.x - st.bw / 2, topY);
      ctx.lineTo(st.x, topY - st.bh * 0.5);
      ctx.lineTo(st.x + st.bw / 2, topY);
      ctx.fill();
    }
  }

  // warm window lights — twinkle subtly at higher levels (additive)
  ctx.globalCompositeOperation = 'lighter';
  for (let s = 0; s < layout.structures.length; s++) {
    const st = layout.structures[s];
    if (st.windows.length === 0) continue;
    const topY = ridgeYAt(st.x) - st.bh;
    for (let k = 0; k < st.windows.length; k++) {
      const win = st.windows[k];
      const tw = layout.twinkleWindows
        ? (t === 0 ? 0.85 : 0.6 + 0.4 * Math.sin(t * 2 + winIndex * 1.3))
        : 0.85;
      winIndex++;
      ctx.fillStyle = `rgba(255, ${190 + Math.round(win.warm * 40)}, ${110 + Math.round(win.warm * 50)}, ${(0.55 * tw).toFixed(3)})`;
      ctx.fillRect(st.x + win.dx, topY + win.dy, 1.6, 1.6);
    }
  }
  ctx.globalCompositeOperation = 'source-over';

  // Capital beacon — a pulsing aircraft-warning light atop the tallest tower
  if (layout.beacon) {
    const bx = layout.cityX;
    const by = ridgeYAt(bx) - h * layout.maxH - 4;
    const pulse = t === 0 ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.4);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const bg = ctx.createRadialGradient(bx, by, 0, bx, by, 12);
    bg.addColorStop(0, `rgba(255, 70, 60, ${(0.5 + pulse * 0.45).toFixed(3)})`);
    bg.addColorStop(1, 'rgba(255, 70, 60, 0)');
    ctx.fillStyle = bg;
    ctx.fillRect(bx - 12, by - 12, 24, 24);
    ctx.beginPath();
    ctx.arc(bx, by, 2, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255, 120, 110, ${(0.7 + pulse * 0.3).toFixed(3)})`;
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// DOCKED scene — station bay silhouette (hex glyph language, scaled up)
// ---------------------------------------------------------------------------

function drawDockedScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sectorId: number,
  t: number,
  isSpaceDock: boolean
): void {
  // 1) Deep space behind the bay + dimmed sector starfield
  ctx.fillStyle = '#040711';
  ctx.fillRect(0, 0, w, h);
  const starRng = splitmix32(sectorId * 7 + 11);
  ctx.fillStyle = '#ffffff';
  for (let i = 0; i < 90; i++) {
    const x0 = starRng() * w;
    const y0 = starRng() * h;
    const size = 0.3 + starRng() * 1.1;
    const bright = (0.25 + starRng() * 0.5) * 0.4;
    const x = (((x0 - t * 0.8) % w) + w) % w;
    ctx.globalAlpha = bright;
    ctx.beginPath();
    ctx.arc(x, y0, size, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // Guide-light accent: SpaceDocks run blue, trading stations run green —
  // the same color split the legacy bay lights used.
  const ac = isSpaceDock ? { r: 0, g: 217, b: 255 } : { r: 0, g: 255, b: 65 };
  const cx = w * 0.5;
  const cy = h * 0.52;
  const R = Math.min(w, h) * 0.52;

  // 2) Ambient bay floodlight from above
  const amb = ctx.createRadialGradient(cx, -h * 0.2, 0, cx, -h * 0.2, h * 1.4);
  amb.addColorStop(0, `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.1)`);
  amb.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = amb;
  ctx.fillRect(0, 0, w, h);

  const hexPath = (r: number, rot: number) => {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = rot + (Math.PI / 3) * i;
      const px = cx + r * Math.cos(a);
      const py = cy + r * Math.sin(a) * 0.92; // slight perspective squash
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();
  };

  // 3) Outer hull silhouette — static structural mass
  const hullRot = Math.PI / 6;
  hexPath(R, hullRot);
  ctx.fillStyle = 'rgba(13, 20, 34, 0.96)';
  ctx.fill();
  ctx.strokeStyle = `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.35)`;
  ctx.lineWidth = 2;
  ctx.stroke();

  // Hull panel seams, clipped to the silhouette
  ctx.save();
  hexPath(R, hullRot);
  ctx.clip();
  ctx.strokeStyle = 'rgba(120, 150, 190, 0.1)';
  ctx.lineWidth = 1;
  for (let y = cy - R; y <= cy + R; y += 26) {
    ctx.beginPath();
    ctx.moveTo(cx - R, y);
    ctx.lineTo(cx + R, y);
    ctx.stroke();
  }
  ctx.restore();

  // 4) Slowly rotating inner habitat ring + radial trusses
  const ringRot = hullRot + t * 0.03;
  hexPath(R * 0.62, ringRot);
  ctx.strokeStyle = `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.22)`;
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.strokeStyle = 'rgba(90, 110, 140, 0.4)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 6; i++) {
    const a = ringRot + (Math.PI / 3) * i;
    ctx.beginPath();
    ctx.moveTo(cx + R * 0.3 * Math.cos(a), cy + R * 0.3 * Math.sin(a) * 0.92);
    ctx.lineTo(cx + R * 0.62 * Math.cos(a), cy + R * 0.62 * Math.sin(a) * 0.92);
    ctx.stroke();
  }

  // 5) Core glow
  const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.3);
  core.addColorStop(0, `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.5)`);
  core.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(cx, cy, R * 0.3, 0, Math.PI * 2);
  ctx.fill();

  // 6) Docking bay aperture on the near face, with blinking guide lights
  const bayW = R * 0.78;
  const bayH = Math.max(14, R * 0.16);
  const bayY = cy + R * 0.46;
  ctx.fillStyle = 'rgba(2, 4, 9, 0.95)';
  ctx.fillRect(cx - bayW / 2, bayY - bayH / 2, bayW, bayH);
  ctx.strokeStyle = `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.5)`;
  ctx.lineWidth = 1;
  ctx.strokeRect(cx - bayW / 2 + 0.5, bayY - bayH / 2 + 0.5, bayW - 1, bayH - 1);
  const lights = 5;
  for (let i = 0; i < lights; i++) {
    const lx = cx - bayW / 2 + bayW * ((i + 0.5) / lights);
    const on = Math.sin(t * 3 + i * 0.9) > 0.35;
    ctx.beginPath();
    ctx.arc(lx, bayY, 2.2, 0, Math.PI * 2);
    ctx.fillStyle = on
      ? `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.95)`
      : `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.18)`;
    ctx.fill();
    if (on) {
      const lg = ctx.createRadialGradient(lx, bayY, 0, lx, bayY, 8);
      lg.addColorStop(0, `rgba(${ac.r}, ${ac.g}, ${ac.b}, 0.45)`);
      lg.addColorStop(1, 'rgba(0, 0, 0, 0)');
      ctx.fillStyle = lg;
      ctx.beginPath();
      ctx.arc(lx, bayY, 8, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // 7) Slow drifting dust motes inside the bay volume
  const moteRng = splitmix32(sectorId * 271 + 99);
  ctx.fillStyle = '#cfd8e6';
  for (let i = 0; i < 36; i++) {
    const baseX = moteRng() * w;
    const y = moteRng() * h;
    const speed = 1.5 + moteRng() * 3;
    const size = 0.5 + moteRng() * 1.1;
    const alpha = 0.08 + moteRng() * 0.18;
    const x = (((baseX + t * speed) % w) + w) % w;
    const bob = Math.sin(t * 0.4 + i) * 2;
    ctx.globalAlpha = alpha;
    ctx.fillRect(x, y + bob, size, size);
  }
  ctx.globalAlpha = 1;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const BASE_FRAME_MS = 1000 / 24; // drift cap; full 60fps only during hover transitions
// Landed scene runs smooth: its per-frame work is now just the 3 ridge paths +
// small particle/cloud/twinkle math (all static geometry + gradients are cached),
// so it can afford a much higher cadence than the 24fps flight/docked drift cap.
// Measured per-frame draw cost on the cached path is ~0.4ms (max ~1ms), so the
// scene is throttle-bound, not work-bound. The cap must sit BELOW the display
// vsync interval (≈16.7ms on 60Hz) or it quantizes to every-other-vsync = 30fps
// (a 48fps / 20.8ms cap did exactly that). 1000/90 ≈ 11.1ms clears the 60Hz
// vsync every frame → smooth 60fps, and scales up to ~90fps on high-refresh
// panels — all for sub-millisecond main-thread cost. reduced-motion still
// renders a single static frame (no loop).
const LANDED_FRAME_MS = 1000 / 90;

const SolarSystemViewscreen: React.FC<SolarSystemViewscreenProps> = ({
  sectorId,
  sectorType = 'normal',
  sectorName = 'Unknown Sector',
  hazardLevel = 0,
  radiationLevel = 0,
  stations = [],
  planets = [],
  ships = [],
  onEntityClick,
  scene = 'flight',
  isSpaceDock = false,
  planetType,
  habitability,
  citadelLevel,
  landedPlanetId,
  onRequestLand,
  onRequestDock,
  selectedShipId = null
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [system, setSystem] = useState<SystemSnapshot | null>(null);
  const [fetchFailed, setFetchFailed] = useState(false);
  const [fallbackSize, setFallbackSize] = useState({ w: 800, h: 320 });
  const [reducedMotion, setReducedMotion] = useState(
    () => typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );

  const sizeRef = useRef({ w: 0, h: 0 });
  const systemRef = useRef<SystemSnapshot | null>(null);
  const hitTargetsRef = useRef<HitTarget[]>([]);
  const hoverRef = useRef<{ target: HitTarget; mx: number; my: number } | null>(null);
  // Celestial-body info popup (flight scene only) + click-vs-drag tracking
  const [popup, setPopup] = useState<PopupState | null>(null);
  const mouseDownPosRef = useRef<{ x: number; y: number } | null>(null);
  const hoverBoostUntilRef = useRef(0);
  const rafRef = useRef<number | undefined>(undefined);
  const lastDrawRef = useRef(0);
  const reducedMotionRef = useRef(reducedMotion);
  reducedMotionRef.current = reducedMotion;
  const envRef = useRef({ hazardLevel, radiationLevel });
  envRef.current = { hazardLevel, radiationLevel };
  // Landed five-axis context: star kind/color + this planet's orbit come from
  // the live /system snapshot (matched by landedPlanetId); habitability +
  // citadel level are dashboard-supplied props. Recomputed each render and
  // ref-mirrored so the rAF loop always reads current values.
  const landedCtx: LandedCtx = (() => {
    const star = system?.star || null;
    let orbitAu: number | undefined;
    let moons: number | undefined;
    let phaseDeg: number | undefined;
    let rings: boolean | undefined;
    let sizeClass: number | undefined;
    let siblings: LandedCtx['siblings'];
    if (landedPlanetId && system?.bodies) {
      const body = system.bodies.find((b) => b.planet_id === landedPlanetId);
      if (body) {
        if (typeof body.orbit_au === 'number') orbitAu = body.orbit_au;
        if (typeof body.moons === 'number') moons = body.moons;
        if (typeof body.phase_deg === 'number') phaseDeg = body.phase_deg;
        if (typeof body.rings === 'boolean') rings = body.rings;
        if (typeof body.size_class === 'number') sizeClass = body.size_class;
      }
      // sibling bodies → distant sky planets (exclude the landed world itself).
      // Cap to a handful so the sky stays uncluttered behind the HUD.
      siblings = system.bodies
        .filter((b) => b.planet_id !== landedPlanetId)
        .slice(0, 5)
        .map((b) => ({
          kind: b.kind,
          sizeClass: typeof b.size_class === 'number' ? b.size_class : 2,
          hue: b.palette?.hue ?? 210,
          sat: b.palette?.sat ?? 40,
          rings: !!b.rings,
        }));
    }
    return {
      habitability,
      citadelLevel,
      starKind: star?.kind,
      starColor: star?.color,
      secondaryColor: star?.secondary?.color,
      orbitAu, // undefined → drawLandedScene falls back to mid 0.5
      moons,
      phaseDeg,
      rings,
      sizeClass,
      landedPlanetId, // seeds time-of-day + landform per world
      siblings,
    };
  })();

  // Scene mode + per-scene parameters, ref-mirrored for the draw loop
  const sceneRef = useRef({ scene, isSpaceDock, palette: landedPalette(planetType), landedCtx });
  sceneRef.current = { scene, isSpaceDock, palette: landedPalette(planetType), landedCtx };

  // Sector ships, ref-mirrored so the draw loop reads the latest poll without
  // restarting the animation effect every 5s.
  const shipsRef = useRef<ShipPresence[]>(ships as ShipPresence[]);
  shipsRef.current = ships as ShipPresence[];
  // COMMS-selected contact's ship id, ref-mirrored for the draw loop.
  const selectedShipRef = useRef<string | null>(selectedShipId);
  selectedShipRef.current = selectedShipId;
  // Ships that have left the sector, animating their departure streak.
  const departuresRef = useRef<ShipDeparture[]>([]);
  // Ships warping IN, animating their arrival streak.
  const arrivalsRef = useRef<ShipArrival[]>([]);
  // Previous ship roster (id → faction color) for departure/arrival diffing.
  const prevShipsRef = useRef<Map<string, string>>(new Map());
  // The sector the previous roster belonged to — so a SECTOR CHANGE (the player
  // warped) doesn't mass-animate the old sector's ships departing and the new
  // sector's ships arriving. Streaks only fire for in-sector roster churn.
  const prevSectorRef = useRef<number | null>(null);

  // Orbital closeup: when set, the windshield zooms to a single planet. The
  // body snapshot + the clicked screen geometry (fromX/Y/R) are captured on
  // entry so the zoom interpolates from the planet's spot in the system view.
  const [orbit, setOrbit] = useState<
    { planetId: string; name: string; body: SystemBody; fromX: number; fromY: number; fromR: number } | null
  >(null);
  const orbitRef = useRef(orbit);
  orbitRef.current = orbit;
  const zoomStartRef = useRef(0);
  const zoomDirRef = useRef(1); // 1 = zooming in, -1 = zooming back out
  const zoomFromTRef = useRef(0); // frozen scene clock during the zoom transition
  // HUD (name card + BACK) reveals only once the zoom-in settles.
  const [hudVisible, setHudVisible] = useState(false);
  // ADR-0073 discoverer rename: draft text + busy/error state for the closeup.
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameDraft, setRenameDraft] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const submitRename = async () => {
    const orb = orbitRef.current;
    const value = renameDraft.trim();
    if (!orb || !value) return;
    setRenameBusy(true);
    setRenameError(null);
    try {
      await apiClient.post(`/api/v1/planets/${orb.planetId}/name`, { name: value });
      // Reflect the new name immediately in the closeup HUD.
      setOrbit((prev) => (prev ? { ...prev, name: value } : prev));
      // Refresh the system snapshot so the system view + label update too.
      apiClient.get(`/api/v1/sectors/${sectorId}/system`)
        .then((res) => { systemRef.current = res.data as SystemSnapshot; setSystem(res.data as SystemSnapshot); })
        .catch(() => {});
      setRenameOpen(false);
      setRenameDraft('');
    } catch (e: any) {
      setRenameError(e?.response?.data?.detail || 'Rename failed');
    } finally {
      setRenameBusy(false);
    }
  };

  // ---- Single-frame painter (shared by the loop, resize, and static mode) ----
  const drawNowRef = useRef<() => void>(() => {});
  drawNowRef.current = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const { w, h } = sizeRef.current;
    if (w < 2 || h < 2) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const t = reducedMotionRef.current ? 0 : Date.now() / 1000;
    const mode = sceneRef.current.scene;
    if (mode === 'docked') {
      hitTargetsRef.current.length = 0; // scenes expose no click targets
      drawDockedScene(ctx, w, h, sectorId, t, sceneRef.current.isSpaceDock);
      return;
    }
    if (mode === 'landed') {
      hitTargetsRef.current.length = 0;
      drawLandedScene(ctx, w, h, sectorId, t, sceneRef.current.palette, sceneRef.current.landedCtx);
      return;
    }
    const orb = orbitRef.current;
    if (orb) {
      // Closeup: LAND and BACK are DOM controls, so the canvas exposes no
      // click targets. Animate the zoom (in or out) unless reduced-motion.
      hitTargetsRef.current.length = 0;
      const dur = reducedMotionRef.current ? 0 : 600;
      const raw = dur <= 0 ? 1 : Math.min(1, (Date.now() - zoomStartRef.current) / dur);
      const progress = zoomDirRef.current >= 0 ? raw : 1 - raw;
      if (progress < 1 && orb.fromR > 0.5) {
        // Continuous camera push-in over the FROZEN system scene: scale the
        // whole view up around the clicked planet so the entire system — sun,
        // orbits, ships — zooms toward it, rather than snapping to a closeup.
        const targetCx = w * 0.44;
        const targetCy = h * 0.54;
        const bigR = Math.min(w * 0.30, h * 0.42);
        const ease = 1 - Math.pow(1 - progress, 3);
        const s = 1 + (bigR / orb.fromR - 1) * ease;
        const curCx = orb.fromX + (targetCx - orb.fromX) * ease;
        const curCy = orb.fromY + (targetCy - orb.fromY) * ease;
        ctx.fillStyle = '#040711';
        ctx.fillRect(0, 0, w, h); // cover the area outside the scaled scene
        ctx.save();
        ctx.translate(curCx, curCy);
        ctx.scale(s, s);
        ctx.translate(-orb.fromX, -orb.fromY);
        drawScene(
          ctx, w, h, sectorId, systemRef.current, zoomFromTRef.current,
          hitTargetsRef.current, null,
          envRef.current.hazardLevel, envRef.current.radiationLevel,
          shipsRef.current, []
        );
        ctx.restore();
        hitTargetsRef.current.length = 0; // scaled hit coords are meaningless
        return;
      }
      drawOrbitCloseup(ctx, w, h, sectorId, orb.body, t, 1, orb.fromX, orb.fromY, orb.fromR);
      return;
    }
    drawScene(
      ctx, w, h, sectorId, systemRef.current, t,
      hitTargetsRef.current, hoverRef.current,
      envRef.current.hazardLevel, envRef.current.radiationLevel,
      shipsRef.current, departuresRef.current, selectedShipRef.current,
      arrivalsRef.current
    );
  };

  // ---- Fetch the system snapshot on sector change (flight scenes only:
  //      docked/landed scenes are pure canvas paint, no telemetry needed) ----
  useEffect(() => {
    let cancelled = false;
    setSystem(null);
    systemRef.current = null;
    setFetchFailed(false);
    hoverRef.current = null;
    // The landed scene also needs the snapshot (for star kind/color + this
    // planet's orbit_au → distance-to-sun). The docked scene is pure paint.
    if (scene !== 'flight' && scene !== 'landed') {
      return;
    }
    apiClient
      .get(`/api/v1/sectors/${sectorId}/system`)
      .then((res) => {
        if (cancelled) return;
        systemRef.current = res.data as SystemSnapshot;
        setSystem(res.data as SystemSnapshot);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('SolarSystemViewscreen: system snapshot fetch failed, falling back:', err);
        setFetchFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sectorId, scene]);

  // ---- Body popup + orbital closeup reset on sector/scene change ----
  useEffect(() => {
    setPopup(null);
    setOrbit(null);
    setHudVisible(false);
    departuresRef.current.length = 0;
    arrivalsRef.current.length = 0;
  }, [sectorId, scene]);

  // ---- Detect ships entering/leaving the sector → warp-in / warp-out streaks ----
  useEffect(() => {
    const prev = prevShipsRef.current;
    const list = ships as ShipPresence[];
    const nextIds = new Set<string>();
    list.forEach((s) => { if (s && s.ship_id) nextIds.add(s.ship_id); });
    // Sector change (the player warped) → don't animate the old sector's ships
    // leaving or the new sector's ships arriving; just rebase the roster.
    const sectorChanged = prevSectorRef.current !== sectorId;
    prevSectorRef.current = sectorId;
    if (!sectorChanged && !reducedMotionRef.current && scene === 'flight') {
      const { w, h } = sizeRef.current;
      if (w > 2 && h > 2) {
        const tNow = Date.now() / 1000;
        const size = 6.0 * Math.min(1.5, Math.max(0.8, Math.min(w, h) / 340));
        const STAGGER_MS = 500;
        // Departures: ships gone from the roster streak off (staggered so a
        // batch doesn't all warp out at the same instant).
        let depIndex = 0;
        prev.forEach((color, id) => {
          if (!nextIds.has(id)) {
            const pos = shipPos(shipPlacement(id, w, h), w, h, tNow);
            departuresRef.current.push({
              shipId: id, x: pos.x, y: pos.y, angle: pos.angle,
              color, size, startMs: Date.now() + depIndex * STAGGER_MS,
            });
            depIndex++;
          }
        });
        // Arrivals: ships newly in the roster warp IN from a seeded edge — but
        // ONLY on subsequent polls (prev non-empty), so entering a sector shows
        // its ships in place rather than warping the whole crowd in at once.
        if (prev.size > 0) {
          let arrIndex = 0;
          list.forEach((s) => {
            if (!s || !s.ship_id || prev.has(s.ship_id)) return;
            const place = shipPlacement(s.ship_id, w, h);
            const to = shipPos(place, w, h, tNow);
            const ea = (place.seed % 360) * Math.PI / 180;
            const from = {
              x: w * (0.5 + Math.cos(ea) * 0.7),
              y: h * (0.5 + Math.sin(ea) * 0.7),
            };
            arrivalsRef.current.push({
              shipId: s.ship_id, fromX: from.x, fromY: from.y, toX: to.x, toY: to.y,
              angle: Math.atan2(to.y - from.y, to.x - from.x),
              color: shipFaction(s).color, size, startMs: Date.now() + arrIndex * STAGGER_MS,
            });
            arrIndex++;
          });
        }
      }
    }
    const m = new Map<string, string>();
    list.forEach((s) => { if (s && s.ship_id) m.set(s.ship_id, shipFaction(s).color); });
    prevShipsRef.current = m;
  }, [ships, scene]);

  // Escape dismisses the popup, or backs out of the orbital closeup
  useEffect(() => {
    if (!popup && !orbit) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (popup) setPopup(null);
      else if (orbit) setOrbit(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [popup, orbit]);

  // ---- Selection change → repaint immediately in reduced-motion (static)
  //      mode, where there's no animation loop to pick up the new reticle ----
  useEffect(() => {
    if (reducedMotionRef.current) drawNowRef.current();
  }, [selectedShipId]);

  // ---- Drop the module-level landed-scene cache on unmount so a remount never
  //      reuses CanvasGradient objects bound to the destroyed canvas context.
  //      (drawLandedScene also guards via cache.ctx !== ctx; this is belt-and-
  //      suspenders and frees the cached geometry/gradients promptly.) ----
  useEffect(() => () => { landedCache = null; }, []);

  // ---- Live prefers-reduced-motion tracking (always mounted) ----
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  // ---- Container-sized canvas via ResizeObserver + devicePixelRatio ----
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const apply = () => {
      const rect = container.getBoundingClientRect();
      const w = Math.max(1, Math.floor(rect.width));
      const h = Math.max(1, Math.floor(rect.height));
      sizeRef.current = { w, h };
      const canvas = canvasRef.current;
      if (canvas) {
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
      }
      setFallbackSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
      drawNowRef.current();
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(container);
    return () => ro.disconnect();
  }, [fetchFailed]);

  // ---- Animation loop: 24fps drift, 60fps hover transitions, hidden = paused ----
  useEffect(() => {
    // A failed snapshot drops the flight scene to the SectorViewport (no canvas),
    // but the landed scene keeps painting its self-contained vista (default sun).
    if (fetchFailed && scene === 'flight') return;

    if (reducedMotion) {
      // Static render — no drift, no twinkle. Redraws happen on hover/resize/data.
      // Re-selected whenever reducedMotion flips (it is in this effect's deps),
      // so enabling mid-session stops the loop and disabling restarts it.
      drawNowRef.current();
      return;
    }

    const tick = (now: number) => {
      rafRef.current = requestAnimationFrame(tick);
      const boosted = now < hoverBoostUntilRef.current;
      // The landed scene is now cheap enough to run at a smoother cadence; flight
      // and docked keep the 24fps drift cap (their draw paths are untouched).
      const frameMs = sceneRef.current.scene === 'landed' ? LANDED_FRAME_MS : BASE_FRAME_MS;
      if (!boosted && now - lastDrawRef.current < frameMs) return;
      lastDrawRef.current = now;
      drawNowRef.current();
    };

    const start = () => {
      if (rafRef.current === undefined) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    const stop = () => {
      if (rafRef.current !== undefined) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = undefined;
      }
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };

    document.addEventListener('visibilitychange', onVisibility);
    if (!document.hidden) start();

    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      stop();
    };
    // isSpaceDock is read (via sceneRef) inside the docked draw path, so a
    // SpaceDock↔station change must restart the loop to repaint the bay
    // guide-light tint — otherwise the stale tint persists until another dep
    // changes.
  }, [fetchFailed, system, sectorId, reducedMotion, scene, planetType, isSpaceDock, orbit,
      habitability, citadelLevel, landedPlanetId]);

  // ---- Pointer interaction ----
  // Hit radius: recorded r + 8px slack, with a ~12px minimum effective radius
  // so small bodies stay tappable. Coordinates are CSS pixels on both sides
  // (getBoundingClientRect deltas vs the setTransform(dpr)-drawn targets), so
  // no devicePixelRatio conversion is needed here.
  const hitTest = (mx: number, my: number): HitTarget | null => {
    let best: HitTarget | null = null;
    let bestDist = Infinity;
    for (const target of hitTargetsRef.current) {
      const dx = mx - target.x;
      const dy = my - target.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < Math.max(12, target.r) + 8 && dist < bestDist) {
        best = target;
        bestDist = dist;
      }
    }
    return best;
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const target = hitTest(mx, my);
    const prevName = hoverRef.current?.target.name ?? null;
    hoverRef.current = target ? { target, mx, my } : null;
    // Every body is now clickable (popup), so any hit gets the pointer
    canvas.style.cursor = target ? 'pointer' : 'default';
    if ((target?.name ?? null) !== prevName) {
      hoverBoostUntilRef.current = performance.now() + 350;
    }
    if (reducedMotionRef.current) drawNowRef.current();
  };

  const handleMouseLeave = () => {
    const canvas = canvasRef.current;
    hoverRef.current = null;
    if (canvas) canvas.style.cursor = 'default';
    if (reducedMotionRef.current) drawNowRef.current();
  };

  const handleMouseDown = (event: React.MouseEvent<HTMLCanvasElement>) => {
    mouseDownPosRef.current = { x: event.clientX, y: event.clientY };
  };

  // ---- Orbital closeup enter/exit (animated zoom) ----
  const enterOrbit = (target: HitTarget) => {
    if (target.meta.kind !== 'planet') return;
    const planetId = target.meta.planetId;
    const body = systemRef.current?.bodies.find((b) => b.planet_id === planetId);
    if (!body) return; // no snapshot body → can't render the closeup
    setPopup(null);
    hoverRef.current = null;
    zoomStartRef.current = Date.now();
    zoomDirRef.current = 1;
    zoomFromTRef.current = reducedMotionRef.current ? 0 : Date.now() / 1000;
    // Run the loop at full framerate through the zoom for a smooth push-in.
    hoverBoostUntilRef.current = performance.now() + 700;
    setHudVisible(false);
    setOrbit({
      planetId, name: target.name, body,
      fromX: target.x, fromY: target.y, fromR: target.r
    });
    // Reveal the name card + BACK control once the zoom has essentially landed.
    window.setTimeout(() => setHudVisible(true), 520);
  };
  const exitOrbit = () => {
    if (reducedMotionRef.current) { setOrbit(null); return; }
    // Zoom back out, then drop the closeup once the animation completes.
    zoomStartRef.current = Date.now();
    zoomDirRef.current = -1;
    hoverBoostUntilRef.current = performance.now() + 700;
    setHudVisible(false);
    window.setTimeout(() => setOrbit(null), 600);
  };

  const openPopupFor = (target: HitTarget) => {
    const { w, h } = sizeRef.current;
    // Prefer beside the body; clamp the card fully inside the band
    const left = Math.min(Math.max(6, target.x + target.r + 12), Math.max(6, w - POPUP_W - 6));
    const top = Math.min(Math.max(6, target.y - POPUP_H / 2), Math.max(6, h - POPUP_H - 6));
    setPopup({ key: popupKeyFor(target), target, left, top });
  };

  const handleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Drag guard: a press that traveled >5px before release is not a click
    const down = mouseDownPosRef.current;
    mouseDownPosRef.current = null;
    if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) return;
    // Popups belong to the flight spectacle only (docked/landed scenes
    // clear their hit targets every frame anyway — belt and suspenders)
    if (scene !== 'flight') return;
    // Hit-test from the click's own coordinates rather than trusting hoverRef
    // (which is stale on touch — there is no mousemove before a tap).
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const target = hitTest(mx, my);
    if (!target) {
      setPopup(null);
      return;
    }
    // Clicking a planet zooms the windshield to an orbital closeup of it
    // (the LAND action moves into the closeup HUD). Other bodies keep popups.
    if (target.kind === 'planet' && target.meta.kind === 'planet') {
      enterOrbit(target);
      return;
    }
    if (popup && popup.key === popupKeyFor(target)) {
      // The closing click is consumed — never reopen the same body with it
      setPopup(null);
      return;
    }
    openPopupFor(target);
  };

  // ---- Popup card content, by body kind ----
  const renderPopupContent = (target: HitTarget): React.ReactNode => {
    const meta = target.meta;
    switch (meta.kind) {
      case 'star':
        return (
          <>
            <div className="ssv-popup-title">{meta.label.toUpperCase()}</div>
            <div className="ssv-popup-line">
              <span
                className="ssv-popup-swatch"
                style={{ background: meta.color }}
                aria-hidden="true"
              ></span>
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
        // NOTE: clicking a real planet now enters the orbital closeup
        // (handleClick intercepts kind==='planet'), so this popup branch is a
        // fallback only — kept for the LAND action if a planet popup is ever
        // opened by another path.
        // Owner detail lives on the sector planet snapshot the dashboard
        // already passes (the system snapshot only carries an owned flag)
        const sectorPlanet = planets.find((p) => p && p.id === meta.planetId);
        const ownerName: string | null = meta.owned
          ? (typeof sectorPlanet?.owner_name === 'string' && sectorPlanet.owner_name
              ? sectorPlanet.owner_name
              : 'CLAIMED')
          : null;
        return (
          <>
            <div className="ssv-popup-title">{target.name.toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.planetKind.replace(/_/g, ' ').toUpperCase()}</div>
            {typeof meta.habitability === 'number' && (
              <div className="ssv-popup-line">HABITABILITY {Math.round(meta.habitability)}%</div>
            )}
            {ownerName && <div className="ssv-popup-line">OWNER — {ownerName}</div>}
            {onRequestLand && (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => {
                  setPopup(null);
                  onRequestLand(meta.planetId);
                }}
              >
                🛬 LAND
              </button>
            )}
          </>
        );
      }
      case 'ship':
        return (
          <>
            <div className="ssv-popup-title">{meta.shipName.toUpperCase()}</div>
            <div className="ssv-popup-line">
              <span
                className="ssv-popup-swatch"
                style={{ background: meta.factionColor }}
                aria-hidden="true"
              ></span>
              {meta.factionLabel}
            </div>
            <div className="ssv-popup-line">{meta.shipType.replace(/_/g, ' ').toUpperCase()}</div>
            <div className="ssv-popup-line">
              {meta.isNpc ? 'NPC' : 'PILOT'} — {meta.captain.toUpperCase()}
            </div>
            {meta.isNpc && (
              <div
                className="ssv-popup-status"
                style={{ color: meta.lawful ? '#ffb000' : '#00ff41' }}
              >
                {meta.lawful ? '⚑ LAWFUL TARGET' : '✋ PROTECTED — ATTACK IS A CRIME'}
              </div>
            )}
          </>
        );
      case 'station':
        return (
          <>
            <div className="ssv-popup-title">{target.name.toUpperCase()}</div>
            <div className="ssv-popup-line">{meta.stationType.replace(/_/g, ' ').toUpperCase()}</div>
            {onRequestDock && (
              <button
                type="button"
                className="ssv-popup-action"
                onClick={() => {
                  setPopup(null);
                  onRequestDock(meta.stationId);
                }}
              >
                ⚓ DOCK
              </button>
            )}
          </>
        );
    }
  };

  // ---- Fallback: the viewscreen never breaks ----
  // Only the flight scene falls back to the legacy SectorViewport. The landed
  // scene paints its own vista regardless of the snapshot — if the fetch fails
  // it simply degrades to the default sun (graceful), so it must keep its canvas.
  if (fetchFailed && scene === 'flight') {
    return (
      <div ref={containerRef} className="solar-viewscreen-container">
        <SectorViewport
          sectorType={sectorType}
          sectorName={sectorName}
          hazardLevel={hazardLevel}
          radiationLevel={radiationLevel}
          stations={stations}
          planets={planets}
          width={fallbackSize.w}
          height={fallbackSize.h}
          onEntityClick={onEntityClick}
        />
      </div>
    );
  }

  return (
    <div ref={containerRef} className="solar-viewscreen-container">
      <canvas
        ref={canvasRef}
        className="solar-viewscreen-canvas"
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        onMouseDown={handleMouseDown}
        onClick={handleClick}
      />
      {popup && scene === 'flight' && (
        <div
          className="ssv-popup"
          style={{ left: popup.left, top: popup.top }}
          role="dialog"
          aria-label={`${popup.target.name} details`}
        >
          <button
            type="button"
            className="ssv-popup-close"
            onClick={() => setPopup(null)}
            aria-label="Close details"
          >
            ✕
          </button>
          {renderPopupContent(popup.target)}
        </div>
      )}
      {orbit && hudVisible && scene === 'flight' && (() => {
        // Inline styles (not a CSS class) so the orbital HUD renders correctly
        // even when a modified stylesheet is stale in the dev cache.
        const glass: React.CSSProperties = {
          background: 'rgba(0, 10, 16, 0.72)',
          border: '1px solid rgba(0, 217, 255, 0.45)',
          boxShadow: '0 0 14px rgba(0, 217, 255, 0.18)',
          fontFamily: "'Courier New', monospace",
          color: '#00d9ff',
          backdropFilter: 'blur(3px)'
        };
        const sp = planets.find((p) => p && p.id === orbit.planetId);
        const hab = orbit.body.habitability;
        const ownerName: string | null =
          (typeof sp?.owner_name === 'string' && sp.owner_name)
            ? sp.owner_name
            : (orbit.body.owned ? 'CLAIMED' : null);
        const pop = typeof sp?.population === 'number' ? sp.population : null;
        const fmtPop = (n: number): string =>
          n >= 1e9 ? `${(n / 1e9).toFixed(1)}B`
            : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
            : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K`
            : `${n}`;
        const line: React.CSSProperties = {
          fontSize: 10.5, color: 'rgba(0, 217, 255, 0.85)', letterSpacing: '0.05em'
        };
        return (
          <>
            <button
              type="button"
              onClick={exitOrbit}
              style={{
                position: 'absolute', top: 8, left: 8, zIndex: 6, ...glass,
                padding: '5px 10px', fontSize: 11, letterSpacing: '0.08em',
                cursor: 'pointer', borderRadius: 3
              }}
            >
              ◄ SYSTEM VIEW
            </button>
            <div
              style={{
                // Drop below the top-right HAZARD chip when it's showing so the
                // two never overlap (the chip only renders when hazard > 0).
                position: 'absolute', top: hazardLevel > 0 ? 112 : 8, right: 8,
                zIndex: 6, ...glass,
                padding: '9px 12px', minWidth: 168, maxWidth: 230,
                borderRadius: 4, lineHeight: 1.5
              }}
              role="dialog"
              aria-label={`${orbit.name} orbital view`}
            >
              <div style={{
                fontSize: 14, fontWeight: 700, letterSpacing: '0.06em',
                textShadow: '0 0 10px rgba(0, 217, 255, 0.8)', marginBottom: 4
              }}>
                {orbit.name.toUpperCase()}
              </div>
              <div style={line}>{orbit.body.kind.replace(/_/g, ' ').toUpperCase()}</div>
              {typeof hab === 'number' && (
                <div style={line}>HABITABILITY {Math.round(hab)}%</div>
              )}
              {pop != null && <div style={line}>POPULATION {fmtPop(pop)}</div>}
              {ownerName && <div style={line}>OWNER — {ownerName.toUpperCase()}</div>}
              {onRequestLand && (
                <button
                  type="button"
                  onClick={() => { exitOrbit(); onRequestLand(orbit.planetId); }}
                  style={{
                    marginTop: 9, ...glass, color: '#00ff41',
                    border: '1px solid rgba(0, 255, 65, 0.5)', padding: '6px 10px',
                    fontSize: 11, cursor: 'pointer', borderRadius: 3, width: '100%',
                    letterSpacing: '0.08em'
                  }}
                >
                  🛬 LAND
                </button>
              )}
              {/* ADR-0073: the discoverer may name this world. */}
              {orbit.body.can_rename && !renameOpen && (
                <button
                  type="button"
                  onClick={() => { setRenameDraft(orbit.name); setRenameError(null); setRenameOpen(true); }}
                  style={{
                    marginTop: 7, ...glass, color: '#ffd166',
                    border: '1px solid rgba(255, 209, 102, 0.5)', padding: '6px 10px',
                    fontSize: 11, cursor: 'pointer', borderRadius: 3, width: '100%',
                    letterSpacing: '0.08em'
                  }}
                >
                  ✎ NAME THIS WORLD
                </button>
              )}
              {orbit.body.can_rename && renameOpen && (
                <div style={{ marginTop: 7 }}>
                  <input
                    type="text"
                    value={renameDraft}
                    maxLength={50}
                    autoFocus
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') submitRename(); if (e.key === 'Escape') setRenameOpen(false); }}
                    placeholder="New name…"
                    style={{
                      width: '100%', boxSizing: 'border-box', ...glass,
                      color: '#e8f4ff', border: '1px solid rgba(255, 209, 102, 0.6)',
                      padding: '5px 8px', fontSize: 11, borderRadius: 3,
                      fontFamily: "'Courier New', monospace"
                    }}
                  />
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <button
                      type="button"
                      disabled={renameBusy || !renameDraft.trim()}
                      onClick={submitRename}
                      style={{
                        flex: 1, ...glass, color: '#ffd166',
                        border: '1px solid rgba(255, 209, 102, 0.5)', padding: '5px',
                        fontSize: 11, cursor: 'pointer', borderRadius: 3, letterSpacing: '0.06em'
                      }}
                    >
                      {renameBusy ? '…' : 'SET'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setRenameOpen(false)}
                      style={{
                        flex: 1, ...glass, color: 'rgba(0, 217, 255, 0.8)',
                        border: '1px solid rgba(0, 217, 255, 0.4)', padding: '5px',
                        fontSize: 11, cursor: 'pointer', borderRadius: 3, letterSpacing: '0.06em'
                      }}
                    >
                      CANCEL
                    </button>
                  </div>
                  {renameError && (
                    <div style={{ ...line, color: '#ff6b6b', marginTop: 4 }}>{renameError}</div>
                  )}
                </div>
              )}
            </div>
          </>
        );
      })()}
    </div>
  );
};

export default SolarSystemViewscreen;
