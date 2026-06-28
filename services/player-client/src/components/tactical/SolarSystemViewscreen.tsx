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
  // h is the VISIBLE scene height (container clips to calc(100% - var(--deck-h))),
  // so h/2 lands at the visual centre regardless of the deck-h setting.
  const starY = h / 2 + (anchorRng() - 0.5) * 2 * (h * 0.04);
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
        // warm→dark depth ramp so the three ridge layers separate as DEPTH (far
        // ridge warmer/lighter → front ridge near-black), not an identical triad.
        ridges: ['#3a1510', '#261008', '#140806'],
        flourish: 'VOLCANIC', flora: '90, 120, 70'
      };
    case 'ICE':
      return {
        skyTop: '#0c1622', skyMid: '#27435c', horizon: '#bcdcec',
        glow: 'rgba(225, 245, 255, 0.5)', haze: '215, 235, 248',
        // frozen palette: blue glacial-ice depths in back → pale snow-white in the
        // FRONT (ridges[2] is the foreground ridge, drawn last/on top).
        ridges: ['#7fa6c4', '#b6d2e4', '#e6f1f8'],
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

// ---------------------------------------------------------------------------
// LIVE DAY/NIGHT CYCLE — the landed sky is now a continuous cycle. A full
// day→night→day takes DAY_CYCLE_SECONDS (~6 min) so it's visibly moving over a
// session. Each world starts at a seeded phase offset so two worlds read at
// different times. EVERYTHING (sky colour, star alpha, sun + body arcs, lighting)
// is driven from the sun altitude derived here — nothing time-of-day is static.
// ---------------------------------------------------------------------------
const DAY_CYCLE_SECONDS = 360;
/** Frozen reduced-motion phase: a pleasant high-morning sun, calm + stable. */
const FROZEN_DAY_PHASE = 0.40;

type DayCycle = {
  dayPhase: number;   // 0..1: 0=midnight, 0.25=sunrise, 0.5=noon, 0.75=sunset
  sunAlt: number;     // -1 (deep below) … +1 (zenith)
  sunUp: boolean;     // sun disc above the horizon
  bright: number;     // 0 (deep night) … 1 (full day) — smooth twilight ramp
  warm: number;       // 0 … 1 extra warm bias near sunrise/sunset
  skyDim: number;     // 0 (noon) … 1 (midnight) darkening of the whole scene
  bodyBright: number; // moon/planet prominence: 1 at night → faint by day
};

/** Per-world seeded phase offset (where in the cycle this world starts). */
function dayPhaseOffsetFor(worldSeed: number): number {
  return splitmix32(worldSeed * 2246822519 + 101)();
}

/** Resolve the live day-cycle factors at time t (seconds). Reduced-motion (t=0)
 *  freezes at FROZEN_DAY_PHASE (a stable daytime frame). */
function dayCycleAt(t: number, phaseOffset: number): DayCycle {
  const dayPhase = t === 0
    ? FROZEN_DAY_PHASE
    : (((t / DAY_CYCLE_SECONDS) + phaseOffset) % 1 + 1) % 1;
  // sun angle: dayPhase 0.25→0 (rise), 0.5→π/2 (noon), 0.75→π (set), 0→-π/2.
  const sunAngle = (dayPhase - 0.25) * Math.PI * 2;
  const sunAlt = Math.sin(sunAngle);
  const sunUp = sunAlt > 0.02;
  // brightness: smooth ramp around the horizon so twilight is a band, not a step.
  const bright = Math.max(0.06, Math.min(1, 0.5 + sunAlt * 1.4));
  // warm: strongest when the sun is low but up (|alt| small near the horizon).
  const warm = sunUp ? Math.max(0, 1 - Math.abs(sunAlt) * 3.2) : 0;
  const skyDim = Math.max(0, Math.min(0.82, 0.5 - sunAlt * 0.95));
  const bodyBright = Math.max(0.18, Math.min(1, 0.55 - sunAlt * 0.85));
  return { dayPhase, sunAlt, sunUp, bright, warm, skyDim, bodyBright };
}

/** Parametric arc for a celestial body across the sky. Each body advances its own
 *  azimuth with t (rate + seeded offset); altitude is the sin of that azimuth so
 *  it rises on one side, crosses, and sets on the other. Returns screen x/y plus
 *  an above-horizon flag + a near-horizon fade factor. Reduced-motion (t=0): the
 *  body sits at a fixed daytime position from its offset only. */
function bodyArcPos(
  t: number, rate: number, phaseOffset: number, azDir: number,
  w: number, horizonY: number
): { x: number; y: number; alt: number; up: boolean; fade: number; azFrac: number } {
  const phase = t === 0
    ? (phaseOffset)
    : ((((t / (DAY_CYCLE_SECONDS * rate)) + phaseOffset) % 1) + 1) % 1;
  const ang = phase * Math.PI * 2;
  const alt = Math.sin(ang);                 // -1..1
  // azimuth maps to x across the screen (with some margin); azDir flips direction.
  const az = phase;                          // 0..1 left→right (or reversed)
  const xu = azDir > 0 ? az : 1 - az;
  const x = w * (0.06 + xu * 0.88);
  // altitude → height above the horizon (upper sky only).
  const y = horizonY - Math.max(0, alt) * horizonY * 0.78;
  const up = alt > 0.0;
  // atmospheric extinction: fade out near the horizon, full strength up high.
  const fade = Math.max(0, Math.min(1, alt * 3.0));
  return { x, y, alt, up, fade, azFrac: xu };
}

/** Unit sky-direction vector for a body at altitude alt (-1..1 → sin of the alt
 *  angle) and azimuth fraction azFrac (0..1 → 0..π across the visible sky). Used to
 *  compute the true angular separation between a moon and the sun (dot product). */
function skyDir(alt: number, azFrac: number): { x: number; y: number; z: number } {
  const altAng = alt * (Math.PI / 2);   // -90°..+90°
  const azAng = azFrac * Math.PI;       // 0..180° across the dome
  const ca = Math.cos(altAng);
  return { x: ca * Math.cos(azAng), y: ca * Math.sin(azAng), z: Math.sin(altAng) };
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
/** Biome-vocabulary structure kinds. Each biome's citadel composes from its own
 *  subset (see biomeArch). signature flags the one landmark structure. */
type CitadelKind =
  | 'BOX'        // generic boxy habitat block
  | 'DOME'       // rounded habitat dome
  | 'GEODESIC'   // big faceted geodesic dome (barren/gas signature)
  | 'ZIGGURAT'   // stepped pyramid (desert signature)
  | 'WINDTOWER'  // slender desert wind-tower
  | 'SPIRE'      // angular ice spire
  | 'ICEDOME'    // faceted translucent ice dome (ice signature)
  | 'LIGHTHOUSE' // shore lighthouse with a rotating lamp (oceanic signature)
  | 'STILT'      // stilted shore platform
  | 'KEEP'       // stone castle keep (mountainous signature)
  | 'BATTLEMENT' // crenellated rampart tower
  | 'FOUNDRY'    // volcanic foundry tower with lava-glow crown (legacy)
  | 'SMOKESTACK' // industrial vent stack with ember seams (legacy)
  // --- VOLCANIC forge-city vocabulary (pass 2) ---
  | 'SMELTER_CRUCIBLE'    // tapered tower, open molten crucible notch on top (signature)
  | 'BLAST_FURNACE_DOME'  // squat hemispherical furnace, top opening glow
  | 'HEAT_VENT_TOWER'     // slender tower + flared hot funnel cap + glow column
  | 'OBSIDIAN_BUNKER'     // low angular heat-shielded bunker + slit-window strip
  | 'MAGMA_PIPELINE'      // two pillars + a glowing horizontal pipe (L3+ connector)
  | 'CATWALK_GANTRY'      // skeletal column + gantry arm + lit observation pod (L4+)
  // --- OCEANIC coastal-future vocabulary (pass 3) ---
  | 'SEADOME'        // translucent sea-blue glass observation dome (oceanic dome)
  | 'DESAL_TOWER'    // coastal desalination tower: cylinder + external pipes + tank
  | 'STILT_PLATFORM' // raised railed deck on stilts built out over the water
  | 'SAIL_HALL'      // white tensile/sail peaked-roof hall (marina convention look)
  | 'ARCOLOGY'   // green tiered garden tower (terran signature)
  | 'TANK'       // utilitarian storage tank
  | 'MAST';      // antenna/mast (L1 outpost flourish)

type CitadelStructure = {
  kind: CitadelKind;
  x: number; bw: number; bh: number;
  windows: { dx: number; dy: number; warm: number }[];
  signature: boolean;
  /** small per-structure seeded variation (step count, facet count, lean…) */
  v1: number; v2: number;
};

type CitadelLayout = {
  structures: CitadelStructure[];
  beacon: boolean;
  beaconKind: 'AIRCRAFT' | 'LAMP' | 'LAVA_PLUME';   // L5 red blinker / oceanic lamp / volcanic lava-plume
  cityX: number; maxH: number;
  twinkleWindows: boolean;
  biome: LandedPalette['flourish'];
  // palette: lighter-than-ridge body fill + a light/dark face pair + window glow.
  body: string; bodyLight: string; bodyDark: string;
  winGlow: { r: number; g: number; b: number };
  accent: string;                    // signature accent (lava crown, ice sheen, etc.)
};
type HazeSeed = { baseX: number; yFrac: number; w: number; speed: number };
// Atmospheric particle kinds → drive shape/colour/motion of the precomputed seeds.
// SPRAY = oceanic sea-mist at the waterline; MOTE = a few low ground motes (lush).
type ParticleKind = 'EMBER' | 'SNOW' | 'DUST' | 'SPRAY' | 'MOTE' | 'FAINT';
type ParticleSeed = { x: number; y: number; size: number; phase: number; speed: number; drift: number; warm: number };
type CloudSeed = { x: number; yFrac: number; w: number; hFrac: number; speed: number; alpha: number };
type VolcFissure = { x: number; w: number; phase: number };

// --- VOLCANIC scene statics (seeded once; positions/colours fixed, motion per-frame) ---
/** Per-segment molten-line bake: a discrete palette index + local shimmer phase/
 *  freq + a low-freq width multiplier (pinch/pool) + an occasional cooled-crust flag.
 *  Shared by foreground ground cracks AND cone-flank runnels so both get the same
 *  hodge-podge mixed-shade treatment instead of a single global pulse. */
type MoltenSeg = { colorSeed: number; segPhase: number; segFreq: number; wMul: number; crusted: boolean };
/** An irregular rock embedded in / sticking out of a molten line. */
type MoltenRock = {
  segIdx: number;                       // which segment it sits on
  archetype: 'angular' | 'rounded' | 'jagged';
  verts: { a: number; r: number }[];    // seeded polygon offsets (angle, radius mult)
  r: number;                            // base radius (px)
  cooled: boolean;                      // ~20% read as cold basalt (no hot rim)
};
/** A faster-shimmering molten hot-pool node along a line. */
type MoltenPool = { segIdx: number; phase: number };
/** Baked per-line molten detail (cracks + runnels both carry this). */
type MoltenBake = { segs: MoltenSeg[]; rocks: MoltenRock[]; pools: MoltenPool[] };
/** Jagged multi-segment lava crack across the foreground ground. */
type LavaCrack = { pts: { dx: number; jy: number }[]; xFrac: number; len: number; phase: number; branchAt: number; bake: MoltenBake };
/** A flaming rock ejected from the crater on a ballistic arc (volcanic ejecta). */
type LavaBomb = {
  launchPhase: number;                  // 0..1 stagger so bombs don't fire in unison
  period: number;                       // flight period multiplier (per-bomb speed)
  vx0: number;                          // outward horizontal bias (-1..+1)
  vy0: number;                          // launch height factor
  landXFrac: number;                    // where it lands across the width (0..1)
  rockVerts: { a: number; r: number }[];// small seeded polygon
  size: number;                         // 2..4 px
};
/** A sinuous lava river: cubic-bezier control points (x fractions + y fractions). */
type LavaRiver = { p: { xf: number; yf: number }[]; wFar: number; wNear: number; phase: number; seed: number };
/** An ember-fountain vent on the midground ridge: a recycling spark pool. */
type EmberFountain = { xFrac: number; vx: number; vy: number; phase: number };
type VolcanicScene = {
  // distant stratovolcano cone (silhouette pts as fractions of its footprint),
  // summit anchor (x frac, y at horizon), caldera (truncated) flag.
  cone: {
    cxFrac: number; baseHalfFrac: number; peakFrac: number; pts: number[]; caldera: boolean; color: string;
    // lava runnels flowing DOWN the cone flanks from the crater: each is a side
    // (-1 left .. +1 right offset at the rim), a downward length frac, and a wiggle.
    // bake carries the same per-segment mixed-shade detail as the ground cracks.
    runnels: { side: number; lenFrac: number; phase: number; wig: number; bake: MoltenBake }[];
  };
  plume: { puffs: { offX: number; offY: number; r: number; spd: number; phase: number }[] };
  rivers: LavaRiver[];   // legacy (no longer drawn — replaced by cracks + lake)
  lake: { cxFrac: number; cyFrac: number; rxFrac: number; ryFrac: number; cells: { dx: number; dy: number; r: number }[] } | null;
  cracks: LavaCrack[];
  bombs: LavaBomb[];     // caldera ejecta arcing out onto the terrain
  fountains: EmberFountain[];
  crustDark: { xFrac: number; yOff: number; r: number }[];
  crustLava: { xFrac: number; yOff: number; r: number; phase: number }[];
};
/** A moon hanging in the sky: position + radius + the phase geometry that draws
 *  its terminator (lit fraction + the orientation the shadow sweeps from). */
type MoonSeed = {
  r: number;
  /** day-cycle arc params (positions computed per frame; see bodyArcPos) */
  arcRate: number; arcOffset: number; arcDir: number;
  /** 0 = new (dark) … 1 = full (fully lit) */
  illum: number;
  /** angle the lit limb faces (radians) — orients the crescent/gibbous shadow */
  lightAngle: number;
  /** subtle CRT tint "r,g,b" */
  tint: string;
  /** darker "r,g,b" for the mare mottling (precomputed from tint) */
  mareTint: string;
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
  r: number;
  /** day-cycle arc params (positions computed per frame; see bodyArcPos) */
  arcRate: number; arcOffset: number; arcDir: number;
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
      // ash motes + embers → EMBER-STORM; dark smoky-red haze. Volcanic (orange)
      // lightning kicks in at ROUGH+ (restricted to the ash-plume column in draw).
      return { precip: tierN > 0.2 ? 'ash' : 'dust', hazeColor: '120, 70, 60', lightning: mid, sunDimBias: 0.2 };
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
  // day/night cycle (continuous) + landform. Positions/sky colour are per-frame.
  dayPhaseOffset: number;   // per-world seeded start phase of the day cycle
  landform: Landform;
  hasWater: boolean;
  waterTopY: number;        // y where the ocean begins (varies by landform)
  landBaseFrac: number;     // ridge base lift for the variant
  citadelOnWater: boolean;  // suppress citadel/flora that would float on water
  // reflection tint for the water glitter column (position computed per frame)
  reflTint: string;
  // sun (size cached; POSITION + gradients computed per frame from the arc)
  sunR: number; coronaR: number; sunAzDir: number; coreWhite: number;
  // moons hanging in the sky (POSITIONS arc per frame)
  moons: MoonSeed[];
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
    // numeric face base (day-tinted) so the draw can derive strata/rim/wet tones.
    faceRGB: { r: number; g: number; b: number };
    // IRREGULAR sea-facing edge: seeded outcrop/notch offsets from clifftop→waterline.
    // tFrac = how far down the face (0 top .. 1 base); off = px outward(+)/inward(−).
    edgePts: { tFrac: number; off: number }[];
    // horizontal-ish STRATA bands down the face (yFrac 0..1 of the face, tone delta).
    strata: { yFrac: number; tone: number; thick: number }[];
    // subtle vertical FRACTURE cracks on the face (xFrac across the face, depth frac).
    fractures: { xFrac: number; top: number; len: number; wig: number }[];
    // talus ROCKS at the cliff foot in the shallows (xOff from edge, size, yJit).
    talus: { xOff: number; r: number; yJit: number; phase: number }[];
  } | null;
  hasCompanion: boolean; c2side: number; c2r: number; c2: { r: number; g: number; b: number };
  // ridge geometry (noise precomputed; drifted profile recomputed per frame)
  layers: RidgeLayer[];
  period: number;
  ridgeColors: string[];
  // STATIC gradients (coordinate + colour fixed for the session)
  skyGrad: CanvasGradient;
  washGrad: CanvasGradient | null;
  glowGrad: CanvasGradient;
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
  volcanic: VolcanicScene | null;   // full volcanic-only scene statics (else null)
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

  // --- DAY/NIGHT CYCLE (continuous; positions + sky colour computed PER FRAME) ---
  // The cache stores only the per-world seeded phase offset. Static colours below
  // are baked at a neutral mid-day REFERENCE brightness; the live cycle applies
  // per-frame brightness/warmth (sky gradient rebuilt each frame) + a scene dim
  // overlay for night, and arcs the sun/moons/planets. No static time-of-day.
  const dayPhaseOffset = dayPhaseOffsetFor(worldSeed);
  const REF_BRIGHT = 0.7;   // reference for baking static tints (shore/ridge/water)
  const todBright = REF_BRIGHT;
  const sc = hexToRgb(env?.starColor || '#ffd27a');

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

  // --- Sky gradient (BASE/fallback; the LIVE per-frame sky is rebuilt in draw
  //     from the day cycle). Baked at the neutral reference brightness. ---
  const skyTop = shiftHex(pal.skyTop, (REF_BRIGHT - 0.5) * 0.55);
  const skyMid = shiftHex(pal.skyMid, (REF_BRIGHT - 0.5) * 0.6);
  const skyHor = shiftHex(pal.horizon, (REF_BRIGHT - 0.5) * 0.45);
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

  // --- Sun (size cached; POSITION + gradients computed per frame from the arc) ---
  // The sun now travels east→west across the sky on the day-cycle arc; only its
  // size + the seeded azimuth direction are cached. anchorRng is still drawn so
  // downstream seeded layouts (horizon glow x, companion side) stay deterministic.
  const anchorRng = splitmix32(worldSeed * 911 + 3);
  anchorRng();                                   // (was the seeded sunX jitter)
  const sunAzDir = anchorRng() > 0.5 ? 1 : -1;   // which way the sun travels
  const sunR = Math.max(6, Math.min(Math.min(w, h) * 0.13, Math.min(w, h) * (0.018 + prox * 0.06) * profile.corona));
  const coronaR = Math.min(Math.hypot(w, h) * 0.55, sunR * (5 + prox * 4) * profile.corona);
  const coreWhite = Math.round(160 + prox * 95);

  // companion sun (binary): position is relative to the sun, applied per frame.
  let hasCompanion = false, c2side = 1, c2r = 0;
  let c2 = { r: 0, g: 0, b: 0 };
  if (env?.secondaryColor) {
    hasCompanion = true;
    c2 = hexToRgb(env.secondaryColor);
    c2side = anchorRng() > 0.5 ? 1 : -1;
    c2r = sunR * 0.55;
  } else {
    anchorRng();
  }

  // --- MOONS in the sky (seeded params; POSITIONS arc per frame) ---
  const moonCount = env && typeof env.moons === 'number' ? Math.max(0, Math.min(3, Math.round(env.moons))) : 0;
  const moons: MoonSeed[] = [];
  if (moonCount > 0) {
    const mRng = splitmix32(worldSeed * 2654435761 + 777);
    const basePhase = (typeof env?.phaseDeg === 'number' ? env.phaseDeg : 0) * Math.PI / 180;
    const szBias = env && typeof env.sizeClass === 'number' ? 0.7 + Math.min(1, env.sizeClass / 9) * 0.7 : 1.0;
    for (let i = 0; i < moonCount; i++) {
      // per-body arc params (seeded): moons move a bit faster than planets for
      // parallax; each starts at its own phase + travels its own direction.
      const arcRate = 0.7 + mRng() * 0.5;        // < 1 → faster than a full day
      const arcOffset = mRng();
      const arcDir = mRng() > 0.5 ? 1 : -1;
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
      // NOTE: moons never carry rings — rings render only on distant sky-PLANETS.
      moons.push({ r, arcRate, arcOffset, arcDir, illum: ill, lightAngle, tint, mareTint, ring: false });
    }
  }

  // --- Reflection tint for the water glitter (the brightest moon, else the sun).
  //     reflX is now computed per frame from the live light-source position. ---
  let reflTint = `${sc.r}, ${sc.g}, ${sc.b}`;
  if (moons.length > 0) {
    let best = moons[0];
    for (const m of moons) if (m.illum > best.illum) best = m;
    reflTint = best.tint;
  }

  // --- Horizon glow (static position; the sun-hue glow follows the sun per frame). ---
  const gx = w * (0.25 + anchorRng() * 0.5);
  const glowGrad = ctx.createRadialGradient(gx, horizonY, 0, gx, horizonY, Math.max(w, h) * (0.4 + prox * 0.18));
  glowGrad.addColorStop(0, pal.glow);
  glowGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');

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
      // IRREGULAR sea-facing edge: seeded outcrops/notches from clifftop down to the
      // waterline so the silhouette reads as broken rock, not a clean curve.
      const edgePts: { tFrac: number; off: number }[] = [];
      const epN = 6 + Math.floor(hRng() * 3);          // 6..8 break points
      for (let i = 0; i < epN; i++) {
        const tFrac = i / (epN - 1);
        // outcrops jut out near mid-face, notches cut in; smaller near the very top.
        const amp = w * 0.035 * Math.sin(tFrac * Math.PI);    // 0 at ends, max mid
        edgePts.push({ tFrac, off: (hRng() - 0.5) * 2 * amp });
      }
      // horizontal strata bands down the face.
      const strata: { yFrac: number; tone: number; thick: number }[] = [];
      const stN = 4 + Math.floor(hRng() * 3);
      for (let i = 0; i < stN; i++) {
        strata.push({ yFrac: 0.12 + (i + hRng() * 0.5) / (stN + 1), tone: (hRng() - 0.5) * 0.5, thick: 2 + hRng() * 4 });
      }
      // vertical fracture cracks on the face.
      const fractures: { xFrac: number; top: number; len: number; wig: number }[] = [];
      const frN = 3 + Math.floor(hRng() * 3);
      for (let i = 0; i < frN; i++) {
        fractures.push({ xFrac: 0.12 + hRng() * 0.76, top: 0.05 + hRng() * 0.3, len: 0.3 + hRng() * 0.5, wig: 0.3 + hRng() * 0.6 });
      }
      // talus rocks in the shallows at the cliff foot.
      const talus: { xOff: number; r: number; yJit: number; phase: number }[] = [];
      const tlN = 2 + Math.floor(hRng() * 3);
      for (let i = 0; i < tlN; i++) {
        talus.push({ xOff: w * (0.01 + hRng() * 0.07), r: 4 + hRng() * 7, yJit: hRng() * h * 0.04, phase: hRng() * Math.PI * 2 });
      }
      headland = {
        side, landFrac, topProfile, plateauY,
        topColor: `rgb(${top.r}, ${top.g}, ${top.b})`,
        faceColor: `rgb(${face.r}, ${face.g}, ${face.b})`,
        edgeColor: `rgba(${edge.r}, ${edge.g}, ${edge.b}, 0.7)`,
        faceRGB: face, edgePts, strata, fractures, talus,
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
  // Each sibling body ARCS across the sky on its own seeded path (slower than the
  // moons → parallax). Only static params are cached; positions compute per frame.
  // Per-kind colours mirror the flight scene's treatment vocab.
  const skyPlanets: SkyPlanet[] = [];
  const sibs = env?.siblings || [];
  if (sibs.length > 0) {
    const spRng = splitmix32(worldSeed * 6271 + 313);
    for (let i = 0; i < sibs.length; i++) {
      const s = sibs[i];
      const treatment = treatmentFor(s.kind);
      const r = Math.max(5, Math.min(w, h) * (0.012 + Math.min(9, s.sizeClass) / 9 * 0.022));
      // arc params: planets move slower than a full day (rate > 1) for parallax.
      const arcRate = 1.4 + spRng() * 1.2;
      const arcOffset = (i + 0.3) / sibs.length + spRng() * 0.15; // spread starts
      const arcDir = spRng() > 0.5 ? 1 : -1;
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
      // base distance dimming (smaller/farther bodies fainter); the live day-cycle
      // brightness is multiplied in per frame at draw time.
      const alpha = 0.4 + Math.min(9, s.sizeClass) / 9 * 0.3;
      skyPlanets.push({ r, arcRate, arcOffset, arcDir, treatment, hue, sat, baseColor, bandColor, rimColor, rings: s.rings, alpha });
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

  // --- VOLCANIC fissures (seeded; kept for the ember anchor x-positions) ---
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

  // --- VOLCANIC SCENE statics (seeded once; volcano cone, ash plume, lava rivers,
  //     lava lake, jagged cracks, ember fountains, crust blotches) ---
  let volcanic: VolcanicScene | null = null;
  if (pal.flourish === 'VOLCANIC') {
    const vRng = splitmix32(worldSeed * 2939 + 67);
    // Bake the per-line molten detail shared by ground cracks AND cone runnels:
    // per-segment palette/shimmer/width, 2–4 embedded rocks, 1–2 hot pools. This is
    // ALL static seeded data — per-frame draw only does cheap shimmer math.
    const buildMoltenBake = (segCount: number): MoltenBake => {
      const segs: MoltenSeg[] = [];
      // a smoothed low-freq width walk so the line PINCHES and POOLS (not uniform).
      let wWalk = vRng();
      for (let s = 0; s < segCount; s++) {
        wWalk = wWalk * 0.7 + vRng() * 0.3;            // smoothing → gentle pooling
        segs.push({
          colorSeed: vRng(),
          segPhase: vRng() * Math.PI * 2,
          segFreq: 0.8 + vRng() * 0.8,
          wMul: 0.5 + wWalk * 1.1,                     // ~0.5..1.6
          crusted: vRng() < 0.2,                       // ~20% cooled plates
        });
      }
      const rockN = 2 + Math.floor(vRng() * 3);        // 2..4 embedded rocks
      const rocks: MoltenRock[] = [];
      for (let r = 0; r < rockN; r++) {
        const pick = vRng();
        const archetype: MoltenRock['archetype'] = pick < 0.4 ? 'angular' : pick < 0.75 ? 'rounded' : 'jagged';
        const verts: { a: number; r: number }[] = [];
        if (archetype === 'rounded') {
          const vN = 10 + Math.floor(vRng() * 3);      // 10..12 verts, gentle jitter
          for (let v = 0; v < vN; v++) verts.push({ a: (v / vN) * Math.PI * 2, r: 0.85 + vRng() * 0.30 });
        } else if (archetype === 'angular') {
          const vN = 5 + Math.floor(vRng() * 3);       // 5..7 verts, ±40° angular jitter
          for (let v = 0; v < vN; v++) verts.push({ a: (v / vN) * Math.PI * 2 + (vRng() - 0.5) * 1.4, r: 0.7 + vRng() * 0.6 });
        } else {                                       // jagged: 6 verts w/ 2 inward notches
          const vN = 6;
          for (let v = 0; v < vN; v++) {
            const notch = (v === 1 || v === 4);
            verts.push({ a: (v / vN) * Math.PI * 2 + (vRng() - 0.5) * 0.5, r: notch ? 0.35 + vRng() * 0.2 : 0.95 + vRng() * 0.5 });
          }
        }
        rocks.push({
          segIdx: Math.floor(vRng() * segCount),
          archetype,
          verts,
          r: 2.5 + vRng() * 4,
          cooled: vRng() < 0.2,
        });
      }
      const poolN = 1 + Math.floor(vRng() * 2);        // 1..2 hot pools
      const pools: MoltenPool[] = [];
      for (let p = 0; p < poolN; p++) pools.push({ segIdx: Math.floor(vRng() * segCount), phase: vRng() * Math.PI * 2 });
      return { segs, rocks, pools };
    };
    // HERO stratovolcano — LARGE, fairly central, peak well up into the sky so it
    // clearly dominates the horizon and rises above the ridge line.
    const caldera = landform === 'VOLC_CALDERA' || vRng() < 0.3;
    // Compositional THIRD, never dead-centre: the bright horizon/sun glow sits at
    // mid-sky, so seed the cone to a left or right third where its silhouette
    // stands clear against EMPTY sky (off the sun) rather than washing out.
    let cxFrac = vRng() < 0.5 ? (0.20 + vRng() * 0.14) : (0.66 + vRng() * 0.14);
    const baseHalfFrac = 0.24 + vRng() * 0.10;    // WIDE base (0.24..0.34 of w)
    // KEEP THE WHOLE CONE ON-SCREEN: a wide base seeded to the 0.20 third put the
    // left flank off the left edge (cxFrac - baseHalfFrac < 0 → clipped, hard edge).
    // Pull the centre inward so both flanks taper smoothly within [0.02, 0.98]·w.
    const MARGIN = 0.02;
    cxFrac = Math.max(baseHalfFrac + MARGIN, Math.min(1 - baseHalfFrac - MARGIN, cxFrac));
    const peakFrac = 0.36 + vRng() * 0.12;        // TALL: peak 0.36..0.48 of h above horizon
    // Smooth clean mountain profile (a proper bell), gently asymmetric, light noise
    // so the silhouette reads as a real cone — NOT a noisy lumpy ridge.
    const np = 11;
    const skew = 0.42 + vRng() * 0.16;            // peak position along base (slight asym)
    const noiseAmp = 0.05;
    const pts: number[] = [];
    for (let i = 0; i < np; i++) {
      const u = i / (np - 1);
      // smooth raised-cosine bell skewed toward `skew`.
      const d = u < skew ? (u / skew) : (1 - (u - skew) / (1 - skew));
      let hgt = (0.5 - 0.5 * Math.cos(Math.max(0, d) * Math.PI)); // 0..1 smooth bell, 0 at both base ends
      hgt += (vRng() - 0.5) * noiseAmp;           // faint texture
      if (caldera) hgt = Math.min(hgt, 0.86);     // flat-topped rim
      hgt = Math.max(0, Math.min(1, hgt));
      // The flanks must taper flush to the ground. Forcing the two base ends to 0
      // (no lift, no min-height floor) keeps the silhouette from ending above the
      // base and closing with a vertical cliff on the left/right.
      if (i === 0 || i === np - 1) hgt = 0;
      pts.push(hgt);
    }
    const coneColor = todBright > 0.3 ? 'rgba(40, 28, 32, 0.96)' : 'rgba(26, 16, 20, 0.97)';
    // lava runnels down the flanks from the crater (3–5).
    const runnels: VolcanicScene['cone']['runnels'] = [];
    const runN = 3 + Math.floor(vRng() * 3);
    const RUN_SEGS = 14;   // runnels are sampled into this many segments at draw time
    for (let i = 0; i < runN; i++) {
      runnels.push({ side: (vRng() - 0.5) * 1.6, lenFrac: 0.35 + vRng() * 0.4, phase: vRng() * Math.PI * 2, wig: 0.4 + vRng() * 0.8, bake: buildMoltenBake(RUN_SEGS) });
    }
    // ash plume puffs above the summit — a THICK, TALL billowing column (more puffs,
    // bigger radii, leaning with wind as they rise). drift up + wrap per frame.
    const puffN = 7 + Math.floor(vRng() * 3);
    const windLean = (vRng() - 0.5) * 0.5;        // overall plume lean direction
    const puffs: VolcanicScene['plume']['puffs'] = [];
    for (let i = 0; i < puffN; i++) {
      const up = i / (puffN - 1);
      puffs.push({
        // lean increases with height; wider higher up (billowing).
        offX: windLean * w * 0.16 * up + (vRng() - 0.5) * w * 0.04,
        offY: -h * (0.03 + up * 0.34),
        r: w * (0.045 + up * 0.05 + vRng() * 0.02),
        spd: 5 + vRng() * 7,
        phase: vRng() * Math.PI * 2,
      });
    }
    // lava rivers (1–3): seeded bezier control points midground → foreground.
    const rivers: LavaRiver[] = [];
    const rivN = 1 + Math.floor(vRng() * 3);
    for (let i = 0; i < rivN; i++) {
      const startX = 0.2 + vRng() * 0.6;
      const p = [
        { xf: startX, yf: 0.60 + vRng() * 0.05 },
        { xf: startX + (vRng() - 0.5) * 0.18, yf: 0.72 + vRng() * 0.05 },
        { xf: startX + (vRng() - 0.5) * 0.22, yf: 0.86 + vRng() * 0.04 },
        { xf: startX + (vRng() - 0.5) * 0.26, yf: 1.0 },
      ];
      rivers.push({ p, wFar: 14 + vRng() * 10, wNear: 6 + vRng() * 5, phase: vRng() * Math.PI * 2, seed: (worldSeed * 53 + i * 97 + 11) >>> 0 });
    }
    // HERO crusted lava LAKE (the main molten feature) — large, low in the mid/
    // foreground. Always present on volcanic worlds. 10–14 basalt crust cells.
    let lake: VolcanicScene['lake'] = null;
    {
      const cellN = 10 + Math.floor(vRng() * 5);
      const cells: { dx: number; dy: number; r: number }[] = [];
      for (let i = 0; i < cellN; i++) {
        cells.push({ dx: (vRng() - 0.5) * 1.7, dy: (vRng() - 0.5) * 1.3, r: 0.16 + vRng() * 0.20 });
      }
      // Bias the lake to ONE side of the foreground so the citadel can sit on the
      // OPPOSITE shore (buildings beside the lake, never in it). Still large/visible.
      const lakeSide = vRng() < 0.5 ? -1 : 1;            // -1 left shore, +1 right shore
      const lakeRx = 0.14 + vRng() * 0.05;              // 0.14..0.19 (visible, leaves a wide shore)
      // centre sits in the biased third, far enough that the far rim leaves an
      // opposite shore clear of the lake for the city.
      const lakeCx = lakeSide < 0 ? (0.24 + vRng() * 0.08) : (0.68 + vRng() * 0.08);
      lake = { cxFrac: lakeCx, cyFrac: 0.74 + vRng() * 0.06, rxFrac: lakeRx, ryFrac: 0.05 + vRng() * 0.03, cells };
    }
    // jagged foreground cracks (3–5, min-spread) — SHORT LOCAL fissures in the
    // ground (each hugs the terrain contour at draw time), never wide horizontal
    // spans across multiple peaks.
    const cracks: LavaCrack[] = [];
    const crackN = 3 + Math.floor(vRng() * 3);
    for (let i = 0; i < crackN; i++) {
      const segs = 12 + Math.floor(vRng() * 7);
      const cp: { dx: number; jy: number }[] = [];
      for (let s = 0; s < segs; s++) cp.push({ dx: (s / (segs - 1)), jy: (vRng() - 0.5) });
      cracks.push({
        pts: cp,
        xFrac: (i + 0.5) / crackN + (vRng() - 0.5) * 0.06,  // min-spread across width
        len: w * (0.06 + vRng() * 0.06),                    // SHORT local fissure (~0.06–0.12 w)
        phase: vRng() * Math.PI * 2,
        branchAt: vRng() < 0.6 ? 0.4 + vRng() * 0.3 : -1,
        bake: buildMoltenBake(segs - 1),                    // one seg of detail per drawn span
      });
    }
    // ember-fountain vents on the midground ridge (2–4).
    const fountains: EmberFountain[] = [];
    const fountN = 2 + Math.floor(vRng() * 3);
    for (let i = 0; i < fountN; i++) {
      fountains.push({
        xFrac: 0.15 + vRng() * 0.7,
        vx: (vRng() - 0.5) * 18,
        vy: 60 + vRng() * 50,
        phase: vRng() * Math.PI * 2,
      });
    }
    // caldera LAVA BOMBS — flaming ejecta arcing SHORT off the crater and landing at
    // the BASE of the cone (its own slopes), NOT flung across the scene toward the
    // citadel. Few, slow, calm. Landing x stays within ~±0.04–0.10·w of the crater x.
    const bombs: LavaBomb[] = [];
    const bombN = 2 + Math.floor(vRng() * 3);     // 2..4 bombs (was 5..8)
    for (let i = 0; i < bombN; i++) {
      const rverts: { a: number; r: number }[] = [];
      const bvN = 5 + Math.floor(vRng() * 2);     // small chunky polygon
      for (let v = 0; v < bvN; v++) rverts.push({ a: (v / bvN) * Math.PI * 2 + (vRng() - 0.5) * 0.8, r: 0.7 + vRng() * 0.6 });
      const sideSign = vRng() < 0.5 ? -1 : 1;     // fall to one slope or the other
      const landOff = sideSign * (0.04 + vRng() * 0.06);  // ±0.04..0.10·w from crater
      bombs.push({
        launchPhase: vRng(),                       // stagger
        period: 1.6 + vRng() * 0.8,                // SLOW per-bomb flight (was 0.7..1.3)
        vx0: sideSign * (0.3 + vRng() * 0.4),      // gentle outward bias toward that slope
        vy0: 0.7 + vRng() * 0.4,                   // modest arc height
        landXFrac: Math.max(0.02, Math.min(0.98, cxFrac + landOff)),  // BASE of the cone
        rockVerts: rverts,
        size: 2 + vRng() * 2,                      // 2..4 px
      });
    }
    // front-ridge crust texture: dark cooling-rock blotches + brighter cooled-lava.
    const crustDark: { xFrac: number; yOff: number; r: number }[] = [];
    for (let i = 0; i < 6; i++) crustDark.push({ xFrac: vRng(), yOff: 6 + vRng() * 26, r: 4 + vRng() * 9 });
    const crustLava: { xFrac: number; yOff: number; r: number; phase: number }[] = [];
    for (let i = 0; i < 3; i++) crustLava.push({ xFrac: vRng(), yOff: 8 + vRng() * 22, r: 3 + vRng() * 5, phase: vRng() * Math.PI * 2 });

    volcanic = { cone: { cxFrac, baseHalfFrac, peakFrac, pts, caldera, color: coneColor, runnels }, plume: { puffs }, rivers, lake, cracks, bombs, fountains, crustDark, crustLava };
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
  // VOLCANIC: keep the city on the shore OPPOSITE the lava lake (no buildings in it).
  // The lake is biased to one foreground side at seed time; give the citadel the
  // other side's land window (with a small margin off the lake's near rim).
  if (volcanic && volcanic.lake) {
    const lk = volcanic.lake;
    const lakeRight = lk.cxFrac + lk.rxFrac + 0.05;   // right rim of the lake (+margin)
    const lakeLeft = lk.cxFrac - lk.rxFrac - 0.05;    // left rim of the lake (+margin)
    // lake biased left → city on the right shore; biased right → city on the left.
    citadelLandX = lk.cxFrac < 0.5
      ? { min: w * Math.min(0.92, lakeRight), max: w * 0.96 }
      : { min: w * 0.04, max: w * Math.max(0.08, lakeLeft) };
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
  else if (particleKind === 'SNOW') pCount = 60;   // ICE: a fuller persistent flurry
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
    dayPhaseOffset, landform, hasWater, waterTopY, landBaseFrac, citadelOnWater,
    reflTint,
    sunR, coronaR, sunAzDir, coreWhite,
    moons, skyPlanets, waves, weather, precipSeeds, creatures,
    shoreY, shoreColor, shoreFoamColor, shoreProfile, shoreCurve, farLands, headland,
    hasCompanion, c2side, c2r, c2,
    layers, period, ridgeColors,
    skyGrad, washGrad, glowGrad, waterBand,
    stars, flouraSeeds, citadelLayout, haze3, hazeStrength,
    particles, particleKind, clouds, cloudTint, volcFissures, volcanic,
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

  // --- LIVE DAY/NIGHT CYCLE — resolve the sun altitude + brightness for this
  //     frame; everything (sky, stars, sun + body arcs, lighting) reads from it. ---
  const dc = dayCycleAt(t, cache.dayPhaseOffset);

  // 1) Sky gradient — REBUILT PER FRAME from the palette + the day cycle so the
  //    sky transitions night → dawn(warm) → day(bright) → dusk(warm) → night.
  {
    const b = dc.bright;
    const warm = dc.warm;
    const top = warmHex(shiftHex(pal.skyTop, (b - 0.5) * 0.7), warm * 0.12);
    const mid = warmHex(shiftHex(pal.skyMid, (b - 0.5) * 0.75), warm * 0.6);
    const hor = warmHex(shiftHex(pal.horizon, (b - 0.5) * 0.6), warm);
    const g = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
    g.addColorStop(0, top);
    g.addColorStop(0.6, mid);
    g.addColorStop(1, hor);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }

  // Star-tinted sky wash (cached colour; only shown when the sun is fairly high)
  if (cache.washGrad && dc.bright > 0.4) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = Math.min(1, (dc.bright - 0.4) * 1.6);
    ctx.fillStyle = cache.washGrad;
    ctx.fillRect(0, 0, w, horizonY * 1.15);
    ctx.restore();
  }

  // 1b) WEATHER SKY — shared biome-tinted overcast overlay (storm-grey for rain,
  //     tan for sand, white for snow, smoky-red for ash). Darkens + tints by tier.
  if (cache.weather && cache.weather.skyDarken > 0) {
    drawWeatherSky(ctx, w, horizonY, cache.weather);
  }

  // 2) STARFIELD — layout cached; twinkle per frame. Stars FADE IN at night and
  //    OUT by day: alpha ∝ (1 - sunAltitude). Skip entirely in bright daylight.
  const starVisibility = Math.max(0, Math.min(1, 1 - (dc.sunAlt + 0.15) * 1.3));
  if (cache.stars.length > 0 && starVisibility > 0.02) {
    ctx.save();
    ctx.fillStyle = '#dfe7f5';
    for (let i = 0; i < cache.stars.length; i++) {
      const s = cache.stars[i];
      const tw = t === 0 ? 0.75 : 0.5 + 0.5 * Math.sin(t * s.twSpeed + s.twPhase);
      ctx.globalAlpha = s.baseAlpha * tw * starVisibility;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  // 2b) DRIFTING CLOUD bands (behind the sun, slow parallax). VOLCANIC worlds get
  //     billowing ASH-CLOUD masses (dark grey-brown body + an orange underbelly
  //     'lighter' pass, night-boosted) instead of the soft additive clouds.
  if (cache.clouds.length > 0) {
    const ashClouds = !!cache.volcanic;
    const nightK = 0.5 + (1 - dc.bright) * 0.5;
    ctx.save();
    for (let i = 0; i < cache.clouds.length; i++) {
      const c = cache.clouds[i];
      const span = w * 1.6;
      const cx = (((c.x + t * c.speed) % span) + span) % span - w * 0.3;
      const cw = c.w * (ashClouds ? 1.15 : 1);
      const chh = h * c.hFrac * (ashClouds ? 1.3 : 1);
      const cy = Math.min(horizonY * c.yFrac * 2, horizonY - chh - 4);
      if (ashClouds) {
        // dark billowing body (overlapping ellipses via one squashed radial), then
        // an orange underbelly bloom beneath it (lit from the lava below).
        ctx.globalCompositeOperation = 'source-over';
        const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, cw);
        bg.addColorStop(0, `rgba(48, 38, 36, ${(0.5).toFixed(3)})`);
        bg.addColorStop(1, 'rgba(40, 32, 30, 0)');
        ctx.fillStyle = bg;
        ctx.save(); ctx.translate(cx, cy); ctx.scale(1, chh / cw); ctx.translate(-cx, -cy);
        ctx.fillRect(cx - cw, cy - cw, cw * 2, cw * 2);
        ctx.restore();
        // orange underbelly (additive, night-boosted)
        ctx.globalCompositeOperation = 'lighter';
        const ug = ctx.createRadialGradient(cx, cy + chh * 0.4, 0, cx, cy + chh * 0.4, cw * 0.8);
        // daytime floor so the ash-cloud underbelly stays lava-lit at midday.
        const ubk = 0.08 + 0.10 * (1 - dc.bright);
        ug.addColorStop(0, `rgba(255, 90, 30, ${ubk.toFixed(3)})`);
        ug.addColorStop(1, 'rgba(255, 60, 0, 0)');
        ctx.fillStyle = ug;
        ctx.save(); ctx.translate(cx, cy + chh * 0.4); ctx.scale(1, 0.5); ctx.translate(-cx, -(cy + chh * 0.4));
        ctx.fillRect(cx - cw, cy + chh * 0.4 - cw, cw * 2, cw * 2);
        ctx.restore();
      } else {
        ctx.globalCompositeOperation = 'lighter';
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, cw);
        g.addColorStop(0, `rgba(${cache.cloudTint}, ${c.alpha.toFixed(3)})`);
        g.addColorStop(1, `rgba(${cache.cloudTint}, 0)`);
        ctx.fillStyle = g;
        ctx.save(); ctx.translate(cx, cy); ctx.scale(1, chh / cw); ctx.translate(-cx, -cy);
        ctx.fillRect(cx - cw, cy - cw, cw * 2, cw * 2);
        ctx.restore();
      }
    }
    ctx.restore();
  }

  // 3) THE SUN — ARCS east→west across the sky on the day cycle. x travels with
  //    dayPhase, y follows the altitude (sin). Drawn only when above the horizon;
  //    gradients rebuilt per frame at the live position (2 gradients — cheap).
  const { sunR, coronaR } = cache;
  // azimuth maps to x; altitude → y above the horizon line. Sun sits a touch
  // lower (larger/softer) near the horizon for a sunrise/sunset feel.
  const sunPhase = dc.dayPhase;
  const sunXu = cache.sunAzDir > 0 ? sunPhase : 1 - sunPhase;
  const sunX = w * (0.06 + sunXu * 0.88);
  const sunY = horizonY - Math.max(-0.05, dc.sunAlt) * horizonY * 0.74;
  // TRUE (unclamped) sun position used for lighting the moons — even when the sun
  // is below the horizon at night, the moons are still lit from its real direction.
  const sunWorldX = sunX;
  const sunWorldY = horizonY - dc.sunAlt * horizonY * 0.74;
  if (dc.sunUp) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    // dim near the horizon (extinction) + storm/hurricane veil.
    const horizonFade = Math.max(0.25, Math.min(1, dc.sunAlt * 4));
    const sunDim = (cache.weather ? 1 - cache.weather.skyDarken * 0.8 : 1) * horizonFade;
    const breathe = t === 0 ? 1 : 0.92 + 0.08 * Math.sin(t * 0.5);
    // corona (per-frame gradient at the arc position)
    const coronaGrad = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, coronaR);
    coronaGrad.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(0.35 + prox * 0.35).toFixed(3)})`);
    coronaGrad.addColorStop(0.35, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(0.12 + prox * 0.15).toFixed(3)})`);
    coronaGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);
    ctx.globalAlpha = breathe * sunDim;
    ctx.fillStyle = coronaGrad;
    ctx.fillRect(sunX - coronaR, sunY - coronaR, coronaR * 2, coronaR * 2);
    // bright core disc (per-frame gradient)
    const cw = cache.coreWhite;
    const discGrad = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR);
    discGrad.addColorStop(0, `rgba(${Math.min(255, sc.r + cw * 0.4)}, ${Math.min(255, sc.g + cw * 0.4)}, ${Math.min(255, sc.b + cw * 0.4)}, 0.98)`);
    discGrad.addColorStop(0.6, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0.95)`);
    discGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0.5)`);
    ctx.globalAlpha = sunDim;
    ctx.fillStyle = discGrad;
    ctx.beginPath();
    ctx.arc(sunX, sunY, sunR, 0, Math.PI * 2);
    ctx.fill();
    // companion sun (binary) — positioned relative to the moving primary.
    if (cache.hasCompanion) {
      const { c2, c2side, c2r } = cache;
      const c2x = sunX + sunR * 4.5 * c2side;
      const c2y = sunY + sunR * 1.8;
      const cc = ctx.createRadialGradient(c2x, c2y, 0, c2x, c2y, c2r * 4);
      cc.addColorStop(0, `rgba(${c2.r}, ${c2.g}, ${c2.b}, 0.4)`);
      cc.addColorStop(1, `rgba(${c2.r}, ${c2.g}, ${c2.b}, 0)`);
      ctx.fillStyle = cc;
      ctx.fillRect(c2x - c2r * 4, c2y - c2r * 4, c2r * 8, c2r * 8);
      ctx.beginPath();
      ctx.arc(c2x, c2y, c2r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${Math.min(255, c2.r + 60)}, ${Math.min(255, c2.g + 60)}, ${Math.min(255, c2.b + 60)}, 0.95)`;
      ctx.fill();
    }
    ctx.restore();
  }

  // 3a2) SIBLING PLANETS — distant discs that ARC across the sky (slower than the
  //      moons → parallax). Drawn behind the moons (which are closer).
  if (cache.skyPlanets.length > 0) {
    drawLandedSkyPlanets(ctx, w, horizonY, t, cache, dc);
  }

  // 3b) MOONS — phased discs that ARC across the sky and rise/set.
  if (cache.moons.length > 0) {
    drawLandedMoons(ctx, w, horizonY, t, cache, dc, sunWorldX, sunWorldY, dc.sunAlt, sunXu);
  }

  // 4) Horizon glow (cached base glow + a per-frame sun-hue glow that follows the
  //    sun's azimuth and brightens with the day). ------------------------------
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = Math.max(0.3, dc.bright);
  ctx.fillStyle = cache.glowGrad;
  ctx.fillRect(0, 0, w, h);
  if (dc.sunUp) {
    const shg = ctx.createRadialGradient(sunX, horizonY, 0, sunX, horizonY, Math.max(w, h) * 0.35);
    const sa = prox * profile.hot * 0.22 * Math.max(0.2, dc.bright) * (1 + dc.warm * 0.8);
    shg.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${sa.toFixed(3)})`);
    shg.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);
    ctx.globalAlpha = 1;
    ctx.fillStyle = shg;
    ctx.fillRect(0, 0, w, h);
  }
  ctx.restore();

  // 4a1) VOLCANIC: lava under-glow rising from the horizon (scene lit from below)
  //      then the distant volcano + summit bloom + ash plume (replaces farLands).
  if (cache.volcanic) {
    drawLavaUnderglow(ctx, w, h, horizonY, dc);
    drawVolcanoFarLand(ctx, w, h, horizonY, t, cache.volcanic, dc, cache.weather);
  } else if (!cache.hasWater && cache.farLands.length > 0) {
    // 4a2) DISTANT LANDMASSES for NON-WATER worlds — far mountains/mesas/dunes at
    //      the horizon, off-centre + irregular, drawn behind the foreground ridges.
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
    const crestRGB = reflWaveRGB(cache, dc.sunUp);
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

    // 3) reflection glitter column under the LIVE light source: the sun while it's
    //    up, else the brightest moon (faint). It tracks the moving sun's azimuth.
    //    No light source up → skip the column (only ambient ripples).
    const refl = dc.sunUp ? sunX : w * 0.5;
    const hasLightSource = dc.sunUp || (cache.moons.length > 0 && dc.bodyBright > 0.2);
    const reflBright = dc.sunUp ? Math.max(0.3, dc.bright) : 0.45 * dc.bodyBright;
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

    // --- ROCKY CLIFF rendering (OCEAN_CLIFF headland). The face is no longer a flat
    //     wall: an IRREGULAR jagged sea-facing edge, a vertical sea-lit→wet gradient,
    //     horizontal strata, vertical fractures, and a lit rim give it rock form. ---
    const edgeTopY = topYAt(landEdgeX);
    const edgeSign = isLeft ? 1 : -1;                 // outward direction toward the sea
    // irregular sea-facing edge x at a fractional depth (0 top .. 1 base/waterline).
    const eps = hl.edgePts;
    const edgeXAt = (tFrac: number): number => {
      const f = Math.max(0, Math.min(1, tFrac)) * (eps.length - 1);
      const i0 = Math.floor(f), i1 = Math.min(eps.length - 1, i0 + 1), fr = f - i0;
      const off = eps[i0].off * (1 - fr) + eps[i1].off * fr;
      return landEdgeX + edgeSign * off;
    };
    const edgeYAt = (tFrac: number): number => edgeTopY + tFrac * (h - edgeTopY);
    // trace the cliff BODY path (clifftop surface + irregular drop to the sea).
    const traceCliffBody = () => {
      ctx.beginPath();
      if (isLeft) {
        ctx.moveTo(0, h);
        ctx.lineTo(0, topYAt(0));
        for (let x = 0; x <= landEdgeX; x += 8) ctx.lineTo(x, topYAt(x));
      } else {
        ctx.moveTo(w, h);
        ctx.lineTo(w, topYAt(w));
        for (let x = w; x >= landEdgeX; x -= 8) ctx.lineTo(x, topYAt(x));
      }
      // irregular jagged edge down to the waterline.
      const steps = 14;
      for (let s = 1; s <= steps; s++) { const tf = s / steps; ctx.lineTo(edgeXAt(tf), edgeYAt(tf)); }
      ctx.closePath();
    };

    // 1) cliff FACE — vertical gradient (sea-lit near the top edge → darker toward
    //    the waterline + a darker WET band at the very base) clipped to the body.
    ctx.save();
    traceCliffBody();
    ctx.clip();
    const fc = hl.faceRGB;
    const liftF = (k: number) => `rgb(${Math.min(255, Math.round(fc.r + (255 - fc.r) * k))}, ${Math.min(255, Math.round(fc.g + (255 - fc.g) * k))}, ${Math.min(255, Math.round(fc.b + (255 - fc.b) * k))})`;
    const darkF = (k: number) => `rgb(${Math.round(fc.r * (1 - k))}, ${Math.round(fc.g * (1 - k))}, ${Math.round(fc.b * (1 - k))})`;
    const fg = ctx.createLinearGradient(0, edgeTopY, 0, h);
    fg.addColorStop(0, liftF(0.16));                  // sea-lit upper face
    fg.addColorStop(0.55, hl.faceColor);              // mid body
    fg.addColorStop(0.88, darkF(0.22));               // shaded lower face
    fg.addColorStop(1, darkF(0.42));                  // dark WET band at the base
    ctx.fillStyle = fg;
    ctx.fillRect(0, edgeTopY - h * 0.1, w, h);
    // 1b) horizontal STRATA bands — slightly varied tone across the face width.
    for (const st of hl.strata) {
      const by = edgeTopY + st.yFrac * (h - edgeTopY);
      ctx.fillStyle = st.tone >= 0 ? liftF(st.tone * 0.5) : darkF(-st.tone * 0.5);
      ctx.globalAlpha = 0.35;
      ctx.fillRect(0, by, w, st.thick);
    }
    ctx.globalAlpha = 1;
    // 1c) vertical FRACTURE cracks (seeded, darker hairlines following the face).
    ctx.strokeStyle = darkF(0.5);
    ctx.lineWidth = 1;
    for (const fr of hl.fractures) {
      const cxTop = edgeTopY + fr.top * (h - edgeTopY);
      const len = fr.len * (h - edgeTopY);
      // anchor the crack between the inland edge and the sea edge at its depth.
      const baseX = isLeft ? landEdgeX * (0.2 + fr.xFrac * 0.7) : landEdgeX + (w - landEdgeX) * (1 - (0.2 + fr.xFrac * 0.7));
      ctx.beginPath();
      const segs = 5;
      for (let s = 0; s <= segs; s++) {
        const yy = cxTop + (len * s) / segs;
        const xx = baseX + Math.sin(s * 1.3 + fr.wig * 6) * fr.wig * 4;
        if (s === 0) ctx.moveTo(xx, yy); else ctx.lineTo(xx, yy);
      }
      ctx.stroke();
    }
    ctx.restore();

    // 1d) lit RIM along the irregular sea-facing edge (catches the sky/sea light) so
    //     the cliff reads 3D, not a flat wall.
    ctx.save();
    ctx.strokeStyle = hl.edgeColor;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(edgeXAt(0), edgeYAt(0));
    for (let s = 1; s <= 14; s++) { const tf = s / 14; ctx.lineTo(edgeXAt(tf), edgeYAt(tf)); }
    ctx.stroke();
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

    // 4) SURF at the cliff BASE — waves break HORIZONTALLY against the rock foot at
    //    the waterline (no vertical stripe down the face). Wet sheen on the lowest
    //    face, talus rocks in the shallows, then a whitecap band + spray at the foot.
    // the waterline against the cliff sits in the lower face; the foot of the
    // irregular edge runs from ~tFrac 0.78 to 1.0.
    const footTop = 0.78;
    // 4a) WET SHEEN on the lowest face — a faint cool reflective wash just above the
    //     waterline, clipped to the cliff body.
    ctx.save();
    traceCliffBody();
    ctx.clip();
    const sheenY = edgeYAt(footTop);
    const sh = ctx.createLinearGradient(0, sheenY, 0, h);
    sh.addColorStop(0, 'rgba(150, 190, 205, 0)');
    sh.addColorStop(1, 'rgba(170, 205, 220, 0.22)');
    ctx.fillStyle = sh;
    ctx.fillRect(0, sheenY, w, h - sheenY);
    ctx.restore();

    // 4b) TALUS ROCKS in the shallows at the foot (dark fills + a foam ring each).
    ctx.save();
    for (const tk of hl.talus) {
      const rx = edgeXAt(0.99) + edgeSign * tk.xOff;
      const ry = h - tk.r * 0.6 - tk.yJit;
      ctx.fillStyle = darkF(0.3);
      ctx.beginPath();
      ctx.ellipse(rx, ry, tk.r, tk.r * 0.7, 0, 0, Math.PI * 2);
      ctx.fill();
      // foam ring lapping the rock (gentle pulse, frozen at t===0).
      const ring = t === 0 ? 0.5 : 0.5 + 0.3 * Math.sin(t * 1.8 + tk.phase);
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = cache.shoreFoamColor;
      ctx.globalAlpha = 0.4 * ring;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.ellipse(rx, ry + tk.r * 0.5, tk.r * 1.3, tk.r * 0.5, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
    }
    ctx.restore();

    // 4c) WHITECAP SURF band hugging the cliff foot — horizontal, along the lower
    //     edge, gently breathing (frozen at t===0). Plus a few spray flecks.
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const surfPulse = t === 0 ? 0.6 : 0.55 + 0.18 * Math.sin(t * 1.6);
    // a soft foam wash along the foot.
    ctx.strokeStyle = cache.shoreFoamColor;
    ctx.lineWidth = 5;
    ctx.globalAlpha = 0.45 * surfPulse;
    ctx.beginPath();
    for (let s = 0; s <= 10; s++) {
      const tf = footTop + (1 - footTop) * (s / 10);
      const wob = t === 0 ? 0 : Math.sin(s * 0.9 + t * 2.2) * 2;
      const ex = edgeXAt(tf) + edgeSign * (3 + wob);   // sit just seaward of the rock
      const ey = edgeYAt(tf);
      if (s === 0) ctx.moveTo(ex, ey); else ctx.lineTo(ex, ey);
    }
    ctx.stroke();
    // a brighter crisp whitecap lip on top of the wash.
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.lineWidth = 1.6;
    ctx.globalAlpha = 0.6 * surfPulse;
    ctx.beginPath();
    for (let s = 0; s <= 10; s++) {
      const tf = footTop + (1 - footTop) * (s / 10);
      const wob = t === 0 ? 0 : Math.sin(s * 0.9 + t * 2.2 + 1) * 2.5;
      const ex = edgeXAt(tf) + edgeSign * (3 + wob);
      const ey = edgeYAt(tf);
      if (s === 0) ctx.moveTo(ex, ey); else ctx.lineTo(ex, ey);
    }
    ctx.stroke();
    // spray flecks bursting off the foot (t!==0 only).
    if (t !== 0) {
      ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
      for (let s = 0; s < 5; s++) {
        const tf = footTop + (1 - footTop) * ((s + 0.5) / 5);
        const life = ((t * 0.8 + s * 0.27) % 1);
        const ex = edgeXAt(tf) + edgeSign * (4 + life * 10);
        const ey = edgeYAt(tf) - life * h * 0.04;
        const a = (1 - life) * 0.6 * surfPulse;
        if (a <= 0.03) continue;
        ctx.globalAlpha = a;
        ctx.beginPath();
        ctx.arc(ex, ey, Math.max(0.6, 1.6 - life), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
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
    const isFront = li === cache.layers.length - 1;
    // The FRONT ridge is the GROUND the player stands/builds on — it must NOT drift,
    // or structures + flora anchored to it would bob as crests scroll past. Only the
    // BACK parallax layers drift; the foreground ground line is static.
    const off = isFront ? 0 : t * layer.speed;
    const period = cache.period;
    const n = layer.pts.length;
    const profileXs: number[] | null = isFront ? [] : null;
    // VOLCANIC: capture the crest line so we can rim-glow it (lit from below).
    const crest: number[] | null = cache.volcanic ? [] : null;
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
      if (crest) crest.push(yTop);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = cache.ridgeColors[li];
    ctx.fill();
    if (profileXs) frontProfile = profileXs;
    // VOLCANIC underlit rim — a thin orange glow along this ridge's crest, scaled
    // up at night (the lava under-glow catching the silhouette edge).
    if (crest) {
      const nightK = 0.5 + (1 - dc.bright) * 0.5;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(255, 90, 30, ${(0.16 * nightK).toFixed(3)})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (let xi = 0; xi < crest.length; xi++) {
        const cxp = xi * 8;
        if (xi === 0) ctx.moveTo(cxp, crest[xi]); else ctx.lineTo(cxp, crest[xi]);
      }
      ctx.stroke();
      ctx.restore();
    }
  }
  }
  const ridgeYAt = (x: number): number => {
    if (!frontProfile || frontProfile.length === 0) return h * 0.84;
    const idx = Math.max(0, Math.min(frontProfile.length - 1, Math.round(x / 8)));
    return frontProfile[idx];
  };

  // 5a2) VOLCANIC midground + foreground lava: the crusted LAVA LAKE is the main
  //      molten feature (the free-floating bezier "rivers" are removed — replaced
  //      by the embedded glowing CRACKS drawn in 5c). Plus front-ridge crust texture.
  if (cache.volcanic) {
    const vs = cache.volcanic;
    drawLavaLake(ctx, w, h, t, vs, dc);
    // front-ridge crust texture so the ground isn't flat black.
    const nightK = 0.5 + (1 - dc.bright) * 0.5;
    ctx.save();
    for (const b of vs.crustDark) {
      const bx = w * b.xFrac;
      ctx.fillStyle = 'rgba(14, 8, 8, 0.5)';
      ctx.beginPath();
      ctx.ellipse(bx, ridgeYAt(bx) + b.yOff, b.r, b.r * 0.55, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'lighter';
    for (const b of vs.crustLava) {
      const bx = w * b.xFrac;
      const pulse = t === 0 ? 0.6 : 0.5 + 0.5 * Math.sin(t * 1.5 + b.phase);
      const by = ridgeYAt(bx) + b.yOff;
      const g = ctx.createRadialGradient(bx, by, 0, bx, by, b.r * 2);
      // daytime floor: glows at midday (0.3) + night boost — an active volcano's
      // crust-lava must never vanish in daylight.
      const clk = pulse * (0.3 + 0.4 * (1 - dc.bright));
      g.addColorStop(0, `rgba(255, 110, 40, ${clk.toFixed(3)})`);
      g.addColorStop(1, 'rgba(255, 60, 10, 0)');
      ctx.fillStyle = g;
      ctx.fillRect(bx - b.r * 2, by - b.r * 2, b.r * 4, b.r * 4);
    }
    ctx.restore();
  }

  // 5b) ICE/SNOW treatment on the front ridge — a bright SNOW CAP ribbon along the
  //     crest, a specular sheen highlight, and a few seeded crevasse/crack lines so
  //     the surface reads unmistakably as snow + glacial ice.
  if (cache.iceSheen && !cache.hasWater) {
    ctx.save();
    // 1) snow-cap ribbon: a thick soft white band hugging the crest line, fading
    //    downward into the ice body (so the tops are snow-covered).
    for (let x = 0; x <= w; x += 8) {
      const y = ridgeYAt(x);
      const capH = 7;
      const cg = ctx.createLinearGradient(0, y, 0, y + capH);
      cg.addColorStop(0, 'rgba(248, 253, 255, 0.9)');
      cg.addColorStop(1, 'rgba(230, 244, 252, 0)');
      ctx.fillStyle = cg;
      ctx.fillRect(x, y, 8, capH);
    }
    // 2) specular sheen highlight along the very crest (additive).
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = 'rgba(225, 245, 255, 0.35)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let x = 0; x <= w; x += 8) {
      const y = ridgeYAt(x);
      if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    // 3) glacial CRACKS/crevasse lines — a few seeded blue hairline fractures
    //    descending from the crest into the ice (deterministic via the cache seed).
    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = 'rgba(120, 165, 200, 0.45)';
    ctx.lineWidth = 1;
    const crackN = 5;
    for (let c = 0; c < crackN; c++) {
      const cx = w * ((c + 0.5) / crackN) + Math.sin(c * 12.9898) * w * 0.06;
      const cyTop = ridgeYAt(cx) + 3;
      const len = h * (0.05 + (Math.abs(Math.sin(c * 7.3)) * 0.06));
      const lean = (Math.sin(c * 3.1) * 6);
      ctx.beginPath();
      ctx.moveTo(cx, cyTop);
      ctx.lineTo(cx + lean * 0.5, cyTop + len * 0.5);
      ctx.lineTo(cx + lean, cyTop + len);
      ctx.stroke();
    }
    ctx.restore();
  }

  // 5c) VOLCANIC foreground — jagged glowing lava CRACKS (+ heat shimmer) and
  //     ember-FOUNTAIN vents arcing sparks up. Replaces the old radial-blob fissures.
  if (cache.volcanic) {
    drawVolcCracks(ctx, w, t, cache.volcanic, dc, ridgeYAt);
    drawEmberFountains(ctx, w, h, t, cache.volcanic, dc, cache.weather, ridgeYAt);
    // caldera ejecta arcing out of the crater onto the terrain (in front of cone).
    drawLavaBombs(ctx, w, h, horizonY, t, cache.volcanic, dc, cache.weather);
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

  // 7) CITADEL SKYLINE — layout cached; baseline + key-light + lights per frame.
  //    Key-light direction reads the live sun x; windows glow stronger at night.
  if (citadel > 0 && cache.citadelLayout) {
    const keyDir = dc.sunUp ? Math.sign(sunX - cache.citadelLayout.cityX) || 1 : 1;
    const winNight = 0.45 + (1 - dc.bright) * 0.55; // windows brighter after dark
    drawCitadelSkyline(ctx, h, t, cache.citadelLayout, ridgeYAt, keyDir, winNight);
  }

  // 7d) NIGHT LIGHTING — the ground/ocean/ridges are baked at a daytime reference,
  //     so darken the lower scene as the sun sets (skyDim). The sky itself already
  //     dims via its per-frame gradient, so only the surface band is overlaid here.
  if (dc.skyDim > 0.02) {
    ctx.save();
    const surfTop = horizonY * 0.9;
    const dimGrad = ctx.createLinearGradient(0, surfTop, 0, h);
    if (cache.volcanic) {
      // VOLCANIC: a WARM-dark dim (not cold navy) capped lower so the orange lava
      // emissives bleed through at night instead of going brown/navy.
      const vDim = Math.min(0.45, dc.skyDim);
      dimGrad.addColorStop(0, `rgba(18, 6, 4, 0)`);
      dimGrad.addColorStop(0.25, `rgba(18, 6, 4, ${(vDim * 0.5).toFixed(3)})`);
      dimGrad.addColorStop(1, `rgba(14, 5, 3, ${(vDim * 0.7).toFixed(3)})`);
    } else {
      dimGrad.addColorStop(0, `rgba(6, 9, 18, 0)`);
      dimGrad.addColorStop(0.25, `rgba(6, 9, 18, ${(dc.skyDim * 0.5).toFixed(3)})`);
      dimGrad.addColorStop(1, `rgba(4, 7, 16, ${(dc.skyDim * 0.7).toFixed(3)})`);
    }
    ctx.fillStyle = dimGrad;
    ctx.fillRect(0, surfTop, w, h - surfTop);
    ctx.restore();
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

  // 11) LIGHTNING. VOLCANIC (ROUGH+) → ORANGE-RED branching strikes in the ash-plume
  //     column (jagged polyline + wide glow). TERRAN/JUNGLE → blue-white full-scene
  //     flash. Deterministic timing; reduced-motion (t=0): no lightning.
  if (cache.weather && cache.weather.lightning && t !== 0) {
    if (cache.volcanic) {
      drawVolcLightning(ctx, w, h, t, cache.volcanic, horizonY);
    } else {
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
}

// ---------------------------------------------------------------------------
// VOLCANIC SCENE renderers (volcanic worlds only). All emissive lava/ember/glow
// uses additive 'lighter'; lava glows brighter at night (nightK). Cached statics,
// per-frame motion. Reduced-motion (t=0) → static (no drift/flicker advance).
// ---------------------------------------------------------------------------

/** Lava under-glow rising from the horizon into the lower sky (additive) + the
 *  distant volcano cone, summit bloom and rising ash plume. Replaces farLands. */
function drawVolcanoFarLand(
  ctx: CanvasRenderingContext2D, w: number, h: number, horizonY: number,
  t: number, vs: VolcanicScene, dc: DayCycle, wx: WeatherFx | null
): void {
  const nightK = 0.5 + (1 - dc.bright) * 0.5;     // brighter at night
  const tier = wx ? wx.tierN : 0;
  const c = vs.cone;
  const cx = w * c.cxFrac;
  const baseHalf = w * c.baseHalfFrac;
  const peakH = h * c.peakFrac;
  const n = c.pts.length;
  const baseY = horizonY + 1;
  // resolve silhouette pts to screen; find the summit.
  const sil: { x: number; y: number }[] = [];
  let summitX = cx, summitY = baseY - peakH, summitIdx = 0;
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1);
    const x = cx - baseHalf + u * baseHalf * 2;
    const y = baseY - c.pts[i] * peakH;
    sil.push({ x, y });
    if (y < summitY) { summitY = y; summitX = x; summitIdx = i; }
  }

  // 1) CONE SILHOUETTE — a large solid mountain with ATMOSPHERIC DEPTH. A 2-stop
  //    body (warmer lifted upper face → darker base) gives 3D form; the whole body
  //    is then blended ~20% toward the sky-haze horizon tint so a DISTANT mountain
  //    reads as receding rather than the same near-black as foreground rock. A
  //    two-pass rim (soft wide outer glow + tight bright core) makes the silhouette
  //    edge unmistakable at ANY day phase.
  ctx.save();
  // build the smoothed cone path once (reused for fill + clip-free rim).
  const traceCone = () => {
    ctx.beginPath();
    ctx.moveTo(sil[0].x, baseY);
    for (let i = 0; i < n; i++) {
      if (i === 0) ctx.lineTo(sil[0].x, sil[0].y);
      else { const px = (sil[i - 1].x + sil[i].x) / 2, py = (sil[i - 1].y + sil[i].y) / 2; ctx.quadraticCurveTo(sil[i - 1].x, sil[i - 1].y, px, py); }
    }
    ctx.lineTo(sil[n - 1].x, sil[n - 1].y);
    ctx.lineTo(sil[n - 1].x, baseY);
    ctx.closePath();
  };
  traceCone();
  // sky-haze horizon tint for atmospheric recession (volcanic warm-amber horizon).
  const hazeR = 138, hazeG = 70, hazeB = 46;
  const mix = (a: number, b: number, k: number) => Math.round(a + (b - a) * k);
  // 2-stop warm-dark rock body (upper face lifted + warmer, base darker).
  const day = dc.bright > 0.4;
  const upR = day ? 92 : 60, upG = day ? 62 : 40, upB = day ? 58 : 44;
  const loR = day ? 44 : 26, loG = day ? 28 : 16, loB = day ? 28 : 20;
  const aMix = 0.20;   // 20% toward haze → atmospheric perspective
  const bodyGrad = ctx.createLinearGradient(0, summitY, 0, baseY);
  bodyGrad.addColorStop(0, `rgb(${mix(upR, hazeR, aMix)}, ${mix(upG, hazeG, aMix)}, ${mix(upB, hazeB, aMix)})`);
  bodyGrad.addColorStop(1, `rgb(${mix(loR, hazeR, aMix)}, ${mix(loG, hazeG, aMix)}, ${mix(loB, hazeB, aMix)})`);
  ctx.fillStyle = bodyGrad;
  ctx.fill();
  ctx.restore();

  // two-pass RIM along the upper/sunward silhouette edge.
  ctx.save();
  // (a) wide soft outer glow — lifts the whole edge off the sky.
  ctx.globalCompositeOperation = 'lighter';
  ctx.strokeStyle = `rgba(255, 180, 120, ${(0.16 + nightK * 0.12).toFixed(3)})`;
  ctx.lineWidth = 6;
  ctx.beginPath();
  ctx.moveTo(sil[0].x, sil[0].y);
  for (let i = 1; i < n; i++) ctx.lineTo(sil[i].x, sil[i].y);
  ctx.stroke();
  // (b) tight bright core — a crisp lit edge.
  ctx.globalCompositeOperation = 'source-over';
  ctx.strokeStyle = `rgba(255, 210, 165, ${(0.5 + nightK * 0.25).toFixed(3)})`;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(sil[0].x, sil[0].y);
  for (let i = 1; i < n; i++) ctx.lineTo(sil[i].x, sil[i].y);
  ctx.stroke();
  ctx.restore();

  // 2) LAVA RUNNELS down the flanks from the crater (attached to the mountain).
  //    Each is sampled into a polyline along its quadratic path, then drawn with the
  //    SAME layered molten recipe as the ground cracks — mixed shades, local shimmer,
  //    embedded rocks, crust bridges — so the cone lines no longer read as a flat
  //    neon-sign pulse. Width tapers narrower toward the toe of the flow.
  const runFloor = 0.7 + nightK * 0.4;
  for (const r of c.runnels) {
    const sx0 = summitX + r.side * baseHalf * 0.10;
    const len = peakH * r.lenFrac;
    const sx1 = summitX + r.side * baseHalf * 0.55;     // drift outward down the face
    const midX = (sx0 + sx1) / 2 + r.wig * baseHalf * 0.08;
    const cy0 = summitY + 2;
    const cyMid = summitY + len * 0.5;
    const cy1 = summitY + len;
    // sample the quadratic Bézier (sx0,cy0)→(midX,cyMid control)→(sx1,cy1) into pts.
    const SEG = r.bake.segs.length;
    const pts: { x: number; y: number }[] = [];
    for (let k = 0; k <= SEG; k++) {
      const u = k / SEG, iu = 1 - u;
      const x = iu * iu * sx0 + 2 * iu * u * midX + u * u * sx1;
      const y = iu * iu * cy0 + 2 * iu * u * cyMid + u * u * cy1;
      pts.push({ x, y });
    }
    drawMoltenLine(ctx, pts, r.bake, t, runFloor, 2.8);
  }

  // 3) CRATER — carved NOTCH + glowing molten BOWL inside it (the mouth that makes
  //    "this is a volcano" read), then a summit sky-bloom above. The bowl punches
  //    through DAYLIGHT (its alpha does not depend on night).
  const craterW = baseHalf * (c.caldera ? 0.34 : 0.22);
  const rimY = summitY + craterW * 0.18;          // crater rim sits just below the peak
  // (a) carve a concave NOTCH at the summit — a shallow dark-rock dip so the bowl
  //     reads as sunk into the cone, not a glow stuck on the peak.
  ctx.save();
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = day ? 'rgb(46, 30, 30)' : 'rgb(24, 14, 16)';
  ctx.beginPath();
  ctx.moveTo(summitX - craterW, summitY - 1);
  ctx.quadraticCurveTo(summitX, rimY + craterW * 0.55, summitX + craterW, summitY - 1);
  ctx.lineTo(summitX + craterW, summitY + 1);
  ctx.lineTo(summitX - craterW, summitY + 1);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  // (b) glowing molten BOWL ellipse INSIDE the notch (white-hot → orange →
  //     transparent). High base alpha so it reads in full daylight.
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const craterPulse = t === 0 ? 0.85 : 0.72 + 0.28 * Math.sin(t * 1.3);
  const bowlCy = rimY + craterW * 0.18;
  const bgr = ctx.createRadialGradient(summitX, bowlCy, 0, summitX, bowlCy, craterW * 1.25);
  bgr.addColorStop(0, `rgba(255, 250, 235, ${Math.min(1, 0.95 * craterPulse).toFixed(3)})`); // white-hot core
  bgr.addColorStop(0.35, `rgba(255, 200, 120, ${(0.9 * craterPulse).toFixed(3)})`);
  bgr.addColorStop(0.7, `rgba(255, 110, 30, ${(0.75 * craterPulse).toFixed(3)})`);
  bgr.addColorStop(1, 'rgba(255, 60, 0, 0)');
  ctx.fillStyle = bgr;
  ctx.save();
  ctx.translate(summitX, bowlCy); ctx.scale(1, 0.5); ctx.translate(-summitX, -bowlCy);
  ctx.fillRect(summitX - craterW * 1.3, bowlCy - craterW * 1.3, craterW * 2.6, craterW * 2.6);
  ctx.restore();
  // (c) summit sky-bloom above the bowl — large enough to read at distance, but it
  //     is the BOWL below that defines the mouth.
  const gr = baseHalf * (0.5 + tier * 0.28);
  const bloom = ctx.createRadialGradient(summitX, summitY, 0, summitX, summitY, gr);
  const ba = 0.4 + (1 - dc.bright) * 0.45;
  bloom.addColorStop(0, `rgba(255, 130, 45, ${(ba * 0.6).toFixed(3)})`);
  bloom.addColorStop(0.5, `rgba(255, 70, 20, ${(ba * 0.22).toFixed(3)})`);
  bloom.addColorStop(1, 'rgba(255, 60, 0, 0)');
  ctx.fillStyle = bloom;
  ctx.fillRect(summitX - gr, summitY - gr, gr * 2, gr * 2);
  ctx.restore();

  // 4) ASH+SMOKE PLUME — a DEFINED, TALL billowing COLUMN pouring off the crater.
  //    A real column (~0.35–0.5 canvas height of travel), widening as it rises with
  //    a soft ANVIL flattening near the top; the lower 2–3 puffs are near-opaque
  //    dense bodies (a solid root) with a faint silhouette edge so the billows have
  //    shape. By DAY: light warm-grey smoke at real opacity against the dark/hazy
  //    sky. By NIGHT: darker smoke with an additive ember-underlit base. Wind-drift
  //    only when animating (t===0 → no drift).
  ctx.save();
  const np2 = vs.plume.puffs.length;
  // total column height: a real column, not a blob. Anchored at the crater bowl.
  const colH = h * (0.38 + tier * 0.10);
  for (let i = 0; i < np2; i++) {
    const pf = vs.plume.puffs[i];
    const up = i / (np2 - 1);                       // 0 root … 1 anvil top
    // stack puffs up the column by their ordinal so the geometry is a true column;
    // a slow per-frame rise cycles texture without breaking the column shape.
    const rise = t === 0 ? 0 : ((t * pf.spd * 0.35) % (h * 0.05));
    // anvil: widen toward the top, with extra flattening in the top third.
    const anvil = 1 + up * (1.4 + tier * 0.7);
    const baseR = pf.r * (0.9 + tier * 0.5);
    const pr = baseR * anvil;
    const colX = summitX + pf.offX
      + (t === 0 ? 0 : Math.sin(t * 0.22 + pf.phase) * w * 0.02 * up); // drift grows w/ height
    const colY = rimY - up * colH - rise;
    // dense root → thinning crown.
    const dense = up < 0.35;
    const bodyA = (dense ? 0.78 - up * 0.3 : 0.42 - (up - 0.35) * 0.32) * (0.9 + tier * 0.35);
    const flatY = up > 0.6 ? 0.55 : 0.92;          // anvil flattening near the top
    const bg = ctx.createRadialGradient(colX, colY, 0, colX, colY, pr);
    if (day) {
      bg.addColorStop(0, `rgba(184, 168, 154, ${Math.max(0, bodyA).toFixed(3)})`);
      bg.addColorStop(0.55, `rgba(150, 134, 124, ${Math.max(0, bodyA * 0.55).toFixed(3)})`);
      bg.addColorStop(1, 'rgba(126, 110, 104, 0)');
    } else {
      bg.addColorStop(0, `rgba(78, 64, 62, ${Math.max(0, bodyA).toFixed(3)})`);
      bg.addColorStop(0.55, `rgba(54, 44, 44, ${Math.max(0, bodyA * 0.55).toFixed(3)})`);
      bg.addColorStop(1, 'rgba(38, 30, 30, 0)');
    }
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = bg;
    ctx.save(); ctx.translate(colX, colY); ctx.scale(1.2, flatY); ctx.translate(-colX, -colY);
    ctx.fillRect(colX - pr, colY - pr, pr * 2, pr * 2);
    ctx.restore();
    // faint silhouette edge on the dense lower billows → gives the puffs shape.
    if (dense) {
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = day ? 'rgba(120, 108, 100, 0.28)' : 'rgba(30, 24, 24, 0.4)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(colX, colY, pr * 0.8, pr * 0.8 * flatY, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    // ember-underlit base (additive, night-boosted) on the root puffs.
    if (up < 0.4) {
      ctx.globalCompositeOperation = 'lighter';
      const ea = (0.3 - up * 0.5) * nightK;
      if (ea > 0.01) {
        const eg = ctx.createRadialGradient(colX, colY + pr * 0.3, 0, colX, colY + pr * 0.3, pr * 0.9);
        eg.addColorStop(0, `rgba(255, 95, 30, ${ea.toFixed(3)})`);
        eg.addColorStop(1, 'rgba(255, 60, 0, 0)');
        ctx.fillStyle = eg;
        ctx.fillRect(colX - pr, colY - pr, pr * 2, pr * 2);
      }
    }
  }
  ctx.restore();
}

/** Lava under-glow gradient from the horizon up into the lower sky (additive). */
function drawLavaUnderglow(ctx: CanvasRenderingContext2D, w: number, h: number, horizonY: number, dc: DayCycle): void {
  const nightK = 0.5 + (1 - dc.bright) * 0.5;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const top = horizonY * 0.55;
  const g = ctx.createLinearGradient(0, top, 0, horizonY * 1.02);
  g.addColorStop(0, 'rgba(255, 60, 0, 0)');
  // a daytime floor (~0.10) + night boost so the horizon reads volcanic at dusk/day.
  g.addColorStop(1, `rgba(255, 75, 15, ${(0.10 + 0.10 * nightK).toFixed(3)})`);
  ctx.fillStyle = g;
  ctx.fillRect(0, top, w, horizonY * 1.02 - top);
  ctx.restore();
}

/** Molten lava lake in the midground: a hot glowing surface broken by a CRACKED
 *  BASALT CRUST (dark plates separated by bright glowing seams), bright hot-spots
 *  punching through, and a soft glow halo. Cached cells; slow seam/hotspot pulse. */
function drawLavaLake(ctx: CanvasRenderingContext2D, w: number, h: number, t: number, vs: VolcanicScene, dc: DayCycle): void {
  if (!vs.lake) return;
  const lk = vs.lake;
  const cx = w * lk.cxFrac, cy = h * lk.cyFrac;
  const rx = w * lk.rxFrac, ry = h * lk.ryFrac;
  const nightK = 0.5 + (1 - dc.bright) * 0.5;
  const dayFloor = 0.45 + 0.55 * (1 - dc.bright);     // glows even at midday
  const churn = t === 0 ? 0 : Math.sin(t * 0.7) * 0.05;
  const pulse = t === 0 ? 0.8 : 0.7 + 0.3 * Math.sin(t * 1.2);
  const eRatio = ry / rx;
  ctx.save();

  // 1) wide soft GLOW HALO on the ground/air around the lake (lava emits light) —
  //    daytime floor + night boost.
  {
    ctx.globalCompositeOperation = 'lighter';
    const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, rx * 2.4);
    bg.addColorStop(0, `rgba(255, 100, 30, ${(0.22 * dayFloor).toFixed(3)})`);
    bg.addColorStop(1, 'rgba(255, 60, 0, 0)');
    ctx.fillStyle = bg;
    ctx.fillRect(cx - rx * 2.4, cy - rx * 2.4, rx * 4.8, rx * 4.8);
  }

  // clip the rest to the lake ellipse.
  ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.clip();

  // 2) HOT MOLTEN SURFACE base — bright white-hot core → orange → dark toward the
  //    near rim. Brighter than a flat pool (lava is emissive).
  ctx.globalCompositeOperation = 'source-over';
  const lg = ctx.createRadialGradient(cx, cy - ry * 0.2, 0, cx, cy, rx * 1.05);
  lg.addColorStop(0, 'rgba(255, 240, 200, 1)');     // white-hot
  lg.addColorStop(0.25, 'rgba(255, 180, 70, 1)');
  lg.addColorStop(0.6, 'rgba(255, 95, 25, 1)');
  lg.addColorStop(1, 'rgba(110, 26, 10, 1)');       // cooling near rim
  ctx.fillStyle = lg;
  ctx.fillRect(cx - rx, cy - ry, rx * 2, ry * 2);

  // 3) glowing molten SEAM bed: an additive bright wash UNDER the crust plates so
  //    the gaps between plates read as glowing cracks of molten rock.
  ctx.globalCompositeOperation = 'lighter';
  const seam = ctx.createRadialGradient(cx, cy, 0, cx, cy, rx);
  seam.addColorStop(0, `rgba(255, 210, 120, ${(0.5 * dayFloor).toFixed(3)})`);
  seam.addColorStop(1, `rgba(255, 90, 20, ${(0.2 * dayFloor).toFixed(3)})`);
  ctx.fillStyle = seam;
  ctx.fillRect(cx - rx, cy - ry, rx * 2, ry * 2);

  // 4) CRACKED BASALT CRUST — dark plates of varied size with GLOWING orange seams
  //    along their borders. The plates cover most of the surface; the bright bed
  //    (3) shows through the gaps as molten seams.
  ctx.globalCompositeOperation = 'source-over';
  for (let i = 0; i < lk.cells.length; i++) {
    const ce = lk.cells[i];
    const ex = cx + ce.dx * rx * (1 + churn);
    const ey = cy + ce.dy * ry * (1 - churn);
    const er = ce.r * rx * 1.15;                      // slightly larger → plates pack tighter
    // dark cooled-basalt plate
    ctx.fillStyle = 'rgba(28, 16, 12, 0.94)';
    ctx.beginPath(); ctx.ellipse(ex, ey, er, er * eRatio * 1.2, 0, 0, Math.PI * 2); ctx.fill();
  }
  // glowing seams: bright additive rims around each plate (pulsing).
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < lk.cells.length; i++) {
    const ce = lk.cells[i];
    const ex = cx + ce.dx * rx * (1 + churn);
    const ey = cy + ce.dy * ry * (1 - churn);
    const er = ce.r * rx * 1.15;
    // wide soft seam glow + tight bright seam core.
    ctx.strokeStyle = `rgba(255, 120, 35, ${(0.45 * pulse * dayFloor).toFixed(3)})`;
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.ellipse(ex, ey, er, er * eRatio * 1.2, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = `rgba(255, 220, 150, ${(0.5 * pulse * dayFloor).toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.ellipse(ex, ey, er, er * eRatio * 1.2, 0, 0, Math.PI * 2); ctx.stroke();
  }

  // 5) BRIGHT MOLTEN HOT-SPOTS punching through the crust (a few additive
  //    white-orange blooms), keyed off alternating cells so they're deterministic.
  for (let i = 0; i < lk.cells.length; i += 3) {
    const ce = lk.cells[i];
    const hx = cx + ce.dx * rx * 0.7;
    const hy = cy + ce.dy * ry * 0.7;
    const hr = ce.r * rx * 0.9;
    const hp = t === 0 ? 0.85 : 0.65 + 0.35 * Math.sin(t * 1.6 + i);
    const hg = ctx.createRadialGradient(hx, hy, 0, hx, hy, hr);
    hg.addColorStop(0, `rgba(255, 250, 230, ${(0.85 * hp * dayFloor).toFixed(3)})`);  // white-hot
    hg.addColorStop(0.5, `rgba(255, 150, 50, ${(0.55 * hp * dayFloor).toFixed(3)})`);
    hg.addColorStop(1, 'rgba(255, 80, 20, 0)');
    ctx.fillStyle = hg;
    ctx.fillRect(hx - hr, hy - hr, hr * 2, hr * 2);
  }
  ctx.restore();
}

// Discrete mixed lava palette (NOT a smooth gradient): deep-red → cooled-orange →
// bright-orange → amber → white-hot. Indexed by a per-segment colorSeed so adjacent
// segments JUMP shades — the "hodge-podge mix of different shades" the player wants.
const LAVA_PALETTE: [number, number, number][] = [
  [140, 20, 10],    // deep red (coolest molten)
  [220, 80, 30],    // cooled orange
  [255, 140, 55],   // bright orange
  [255, 200, 100],  // amber
  [255, 245, 200],  // white-hot
];
function lavaShade(colorSeed: number): [number, number, number] {
  // weight toward the mid oranges; white-hot is rarer (a sparse highlight).
  const i = colorSeed < 0.18 ? 0 : colorSeed < 0.45 ? 1 : colorSeed < 0.72 ? 2 : colorSeed < 0.9 ? 3 : 4;
  return LAVA_PALETTE[i];
}

/** Draw ONE molten line (a polyline in screen space) with the layered recipe:
 *  dark halo → thermal-gradient base → per-segment mixed-shade local shimmer →
 *  white-hot core → hot-pool micro-shimmer → crust bridges → embedded rocks.
 *  ALL motion is LOW-amplitude LOCAL shimmer (two incommensurate slow freqs per
 *  segment) — NO global pulse. Shared by ground cracks AND cone runnels.
 *  `dayFloor` = 0.5 + 0.5*(1-bright) keeps lines molten at midday. baseW = base
 *  line width (px). When t===0 every sin() collapses to its phase-only spatial form
 *  so the still frame reads mid-shimmer with spatial variety, not flat. */
function drawMoltenLine(
  ctx: CanvasRenderingContext2D, pts: { x: number; y: number }[], bake: MoltenBake,
  t: number, dayFloor: number, baseW: number
): void {
  const segN = Math.min(pts.length - 1, bake.segs.length);
  if (segN < 1) return;
  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  // 1) DARK HALO — wide dark channel under everything (the carved fissure).
  ctx.globalCompositeOperation = 'source-over';
  ctx.strokeStyle = 'rgba(18, 6, 4, 0.9)';
  ctx.lineWidth = baseW + 3;
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let s = 1; s < pts.length; s++) ctx.lineTo(pts[s].x, pts[s].y);
  ctx.stroke();

  // 2) THERMAL BASE — one luminous gradient floor along the whole line so even dim
  //    segments still read molten (deep-red → amber → deep-red).
  ctx.globalCompositeOperation = 'lighter';
  const a0 = pts[0], aN = pts[pts.length - 1];
  const tg = ctx.createLinearGradient(a0.x, a0.y, aN.x, aN.y);
  const ta = (0.5 * dayFloor).toFixed(3);
  tg.addColorStop(0, `rgba(150, 35, 12, ${ta})`);
  tg.addColorStop(0.5, `rgba(255, 170, 70, ${(0.55 * dayFloor).toFixed(3)})`);
  tg.addColorStop(1, `rgba(150, 35, 12, ${ta})`);
  ctx.strokeStyle = tg;
  ctx.lineWidth = baseW * 1.1;
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let s = 1; s < pts.length; s++) ctx.lineTo(pts[s].x, pts[s].y);
  ctx.stroke();

  // 3) PER-SEGMENT mixed shade + LOW local shimmer (each its own stroke).
  for (let s = 0; s < segN; s++) {
    const sg = bake.segs[s];
    const [r, g, b] = lavaShade(sg.colorSeed);
    // two incommensurate slow freqs, total amplitude <=0.15 → a slow living crawl,
    // never a flicker. t===0 drops the time term (phase-only spatial variety).
    const sh = t === 0
      ? 0.80 + 0.10 * Math.sin(sg.segPhase + s * 1.1) + 0.05 * Math.sin(s * 0.4 + sg.segPhase * 1.7)
      : 0.80 + 0.10 * Math.sin(t * 0.7 * sg.segFreq + sg.segPhase + s * 1.1) + 0.05 * Math.sin(t * 1.3 + s * 0.4 + sg.segPhase * 1.7);
    const a = (sg.crusted ? 0.2 : sh) * dayFloor;
    ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${Math.max(0, a).toFixed(3)})`;
    ctx.lineWidth = Math.max(0.6, baseW * sg.wMul);
    ctx.beginPath();
    ctx.moveTo(pts[s].x, pts[s].y);
    ctx.lineTo(pts[s + 1].x, pts[s + 1].y);
    ctx.stroke();
  }

  // 4) WHITE-HOT CORE — thin full-line specular highlight on top, low alpha.
  ctx.strokeStyle = `rgba(255, 248, 225, ${(0.30 * dayFloor).toFixed(3)})`;
  ctx.lineWidth = Math.max(0.5, baseW * 0.35);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let s = 1; s < pts.length; s++) ctx.lineTo(pts[s].x, pts[s].y);
  ctx.stroke();

  // 5) HOT-POOL MICRO-SHIMMER — small radial blooms at 1–2 nodes, faster local pulse.
  for (const pool of bake.pools) {
    const idx = Math.min(pool.segIdx, pts.length - 1);
    const p = pts[idx];
    const pp = t === 0 ? 0.8 : 0.7 + 0.3 * Math.sin(t * 4.2 + pool.phase);
    const pr = (4 + baseW) + 2;
    const pg = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, pr);
    pg.addColorStop(0, `rgba(255, 230, 175, ${(0.55 * pp * dayFloor).toFixed(3)})`);
    pg.addColorStop(1, 'rgba(255, 120, 40, 0)');
    ctx.fillStyle = pg;
    ctx.fillRect(p.x - pr, p.y - pr, pr * 2, pr * 2);
  }

  // 6) CRUST BRIDGES — dark plates over the ~20% crusted segments so molten glow
  //    only shows in the GAPS between cooled rock.
  ctx.globalCompositeOperation = 'source-over';
  ctx.strokeStyle = 'rgba(28, 14, 10, 0.88)';
  for (let s = 0; s < segN; s++) {
    if (!bake.segs[s].crusted) continue;
    ctx.lineWidth = baseW * bake.segs[s].wMul + 1.2;
    ctx.beginPath();
    ctx.moveTo(pts[s].x, pts[s].y);
    ctx.lineTo(pts[s + 1].x, pts[s + 1].y);
    ctx.stroke();
  }

  // 7) ROCK BLOTS — irregular rocks sticking out of the line. Batched: all dark
  //    fills + shadows first, then all hot rims, to minimise composite switches.
  const traceRock = (cxr: number, cyr: number, rk: MoltenRock) => {
    ctx.beginPath();
    for (let v = 0; v < rk.verts.length; v++) {
      const vx = cxr + Math.cos(rk.verts[v].a) * rk.r * rk.verts[v].r;
      const vy = cyr + Math.sin(rk.verts[v].a) * rk.r * rk.verts[v].r * 0.8;   // squash → sits on ground
      if (v === 0) ctx.moveTo(vx, vy); else ctx.lineTo(vx, vy);
    }
    ctx.closePath();
  };
  const rockPos = (rk: MoltenRock) => {
    const idx = Math.min(rk.segIdx, pts.length - 1);
    return pts[idx];
  };
  // pass A — soft cast shadows beneath each rock.
  ctx.globalCompositeOperation = 'source-over';
  for (const rk of bake.rocks) {
    const p = rockPos(rk);
    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
    ctx.beginPath();
    ctx.ellipse(p.x, p.y + rk.r * 0.5, rk.r * 1.1, rk.r * 0.4, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  // pass B — dark rock bodies.
  for (const rk of bake.rocks) {
    const p = rockPos(rk);
    ctx.fillStyle = 'rgba(30, 14, 8, 0.92)';
    traceRock(p.x, p.y, rk);
    ctx.fill();
    if (rk.cooled) {
      // cold basalt: a faint top-left specular patch, NO hot rim.
      ctx.fillStyle = 'rgba(55, 35, 30, 0.6)';
      ctx.beginPath();
      ctx.ellipse(p.x - rk.r * 0.3, p.y - rk.r * 0.3, rk.r * 0.35, rk.r * 0.25, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  // pass C — additive hot rims where lava meets the (non-cooled) rock.
  ctx.globalCompositeOperation = 'lighter';
  for (const rk of bake.rocks) {
    if (rk.cooled) continue;
    const p = rockPos(rk);
    ctx.strokeStyle = `rgba(255, 120, 35, ${(0.35 * dayFloor).toFixed(3)})`;
    ctx.lineWidth = 1.6;
    traceRock(p.x, p.y, rk); ctx.stroke();
    ctx.strokeStyle = `rgba(255, 220, 160, ${(0.25 * dayFloor).toFixed(3)})`;
    ctx.lineWidth = 0.8;
    traceRock(p.x, p.y, rk); ctx.stroke();
  }

  ctx.restore();
}

/** Jagged glowing lava cracks across the foreground ground + heat shimmer. */
function drawVolcCracks(
  ctx: CanvasRenderingContext2D, w: number, t: number, vs: VolcanicScene, dc: DayCycle,
  ridgeYAt: (x: number) => number
): void {
  const nightK = 0.5 + (1 - dc.bright) * 0.5;
  const dayFloor = 0.5 + 0.5 * (1 - dc.bright);   // floor 0.5 at noon + night boost
  for (let i = 0; i < vs.cracks.length; i++) {
    const cr = vs.cracks[i];
    const x0 = w * cr.xFrac - cr.len / 2;
    // anchor EACH point to the ground at THAT x so the fissure HUGS the terrain
    // contour rather than rendering as one flat horizontal line across peaks.
    const pts: { x: number; y: number }[] = [];
    for (let s = 0; s < cr.pts.length; s++) {
      const px = x0 + cr.pts[s].dx * cr.len;
      pts.push({ x: px, y: ridgeYAt(px) + 6 + cr.pts[s].jy * 5 });
    }
    // the layered molten recipe (mixed shades, local shimmer, rocks, crust bridges).
    drawMoltenLine(ctx, pts, cr.bake, t, dayFloor, 2.2);

    // optional dark-rooted branch with its own small glow.
    if (cr.branchAt > 0) {
      const bi = Math.floor(cr.branchAt * (cr.pts.length - 1));
      const bx = x0 + cr.pts[bi].dx * cr.len;
      const by = ridgeYAt(bx) + 6 + cr.pts[bi].jy * 5;
      ctx.save();
      ctx.lineCap = 'round';
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(255, 150, 60, ${(0.55 * dayFloor).toFixed(3)})`;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(bx, by);
      ctx.lineTo(bx + cr.len * 0.18, by + cr.len * 0.10);
      ctx.lineTo(bx + cr.len * 0.30, by + cr.len * 0.06);
      ctx.stroke();
      ctx.restore();
    }
    // faint heat-shimmer band just above the crack (additive, wobbling) — t!==0 only.
    if (t !== 0) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(255, 140, 60, ${(0.06 * nightK).toFixed(3)})`;
      ctx.lineWidth = 6;
      ctx.beginPath();
      for (let s = 0; s < cr.pts.length; s++) {
        const px = x0 + cr.pts[s].dx * cr.len;
        const py = ridgeYAt(px) - 6 + Math.sin(t * 3 + s * 0.8 + cr.phase) * 2;
        if (s === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.restore();
    }
  }
}

/** CALDERA LAVA BOMBS — small flaming rocks ejected from the crater on ballistic
 *  arcs, landing on the foreground terrain. Each bomb loops on its own staggered
 *  phase: a glowing cooling TRAIL behind it, a dark rock with a hot rim at its head,
 *  a faint smoke puff, and a brief IMPACT FLASH + fading ember as it lands.
 *  Reduced motion (t===0): a couple of static bombs frozen mid-arc, no loop. */
function drawLavaBombs(
  ctx: CanvasRenderingContext2D, w: number, h: number, horizonY: number, t: number,
  vs: VolcanicScene, dc: DayCycle, wx: WeatherFx | null
): void {
  if (vs.bombs.length === 0) return;
  // resolve the crater mouth screen coords (mirror drawVolcanoFarLand's summit math).
  const c = vs.cone;
  const cx = w * c.cxFrac;
  const baseHalf = w * c.baseHalfFrac;
  const peakH = h * c.peakFrac;
  const n = c.pts.length;
  const baseY = horizonY + 1;
  let summitX = cx, summitY = baseY - peakH;
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1);
    const x = cx - baseHalf + u * baseHalf * 2;
    const y = baseY - c.pts[i] * peakH;
    if (y < summitY) { summitY = y; summitX = x; }
  }
  const craterY = summitY + 2;
  const dayK = 0.6 + 0.4 * (1 - dc.bright);
  const tier = wx ? wx.tierN : 0;
  // reduced motion: freeze just the first two bombs at a fixed mid-arc, no loop.
  const reduced = t === 0;
  // SLOW, calm arcs. period multiplies this, so keep the base low; tier adds only a
  // small, capped nudge (never a fast barrage). Effective cycle ≈ 0.18..0.30 · period.
  const speed = 0.18 + Math.min(0.06, tier * 0.04);

  const traceRock = (px: number, py: number, sz: number, verts: { a: number; r: number }[]) => {
    ctx.beginPath();
    for (let v = 0; v < verts.length; v++) {
      const vx = px + Math.cos(verts[v].a) * sz * verts[v].r;
      const vy = py + Math.sin(verts[v].a) * sz * verts[v].r;
      if (v === 0) ctx.moveTo(vx, vy); else ctx.lineTo(vx, vy);
    }
    ctx.closePath();
  };

  ctx.save();
  const count = reduced ? Math.min(2, vs.bombs.length) : vs.bombs.length;
  for (let i = 0; i < count; i++) {
    const bomb = vs.bombs[i];
    // flight progress u: 0 at launch → 1 at landing.
    const u = reduced ? 0.45 : ((t * speed * bomb.period + bomb.launchPhase) % 1 + 1) % 1;
    const landX = bomb.landXFrac * w;
    // land at the FOOT of the distant cone (on the horizon), NOT the foreground ground.
    // ridgeYAt(landX) would drop the bomb all the way down to the near terrain, making it
    // look like it flies a long way to the citadel; the volcano sits back at baseY.
    const landY = baseY - 2;
    // launch a little to the bomb's outward side of the crater mouth.
    const startX = summitX + bomb.vx0 * baseHalf * 0.12;
    const startY = craterY;
    // horizontal: ease from crater to landing. vertical: parabola that rises above
    // the launch then falls to the ground (apex height scaled by vy0).
    const px = startX + (landX - startX) * u;
    // arc height scales with the (short) horizontal span so a near-base landing gets
    // a modest hop, not a tall vertical fountain. Clamped to a small range.
    const span = Math.abs(landX - startX);
    const apex = Math.max(peakH * 0.05, Math.min(peakH * 0.16, span * 0.6)) * bomb.vy0;
    const baseLine = startY + (landY - startY) * u;  // straight crater→land descent
    const py = baseLine - apex * 4 * u * (1 - u);    // parabolic lift (0 at ends)

    // glowing cooling TRAIL behind the bomb (additive; hottest near launch).
    if (!reduced) {
      ctx.globalCompositeOperation = 'lighter';
      const TR = 6;
      for (let k = 1; k <= TR; k++) {
        const tu = Math.max(0, u - k * 0.018);
        const tpx = startX + (landX - startX) * tu;
        const tBase = startY + (landY - startY) * tu;
        const tpy = tBase - apex * 4 * tu * (1 - tu);
        const heat = (1 - tu) * (1 - k / TR);        // cools along flight + along trail
        const ta = 0.28 * heat * dayK;
        if (ta <= 0.01) continue;
        const tr = bomb.size * (1.2 - k * 0.1);
        const tg = ctx.createRadialGradient(tpx, tpy, 0, tpx, tpy, Math.max(1, tr * 2));
        tg.addColorStop(0, `rgba(255, 220, 150, ${ta.toFixed(3)})`);
        tg.addColorStop(1, 'rgba(255, 90, 30, 0)');
        ctx.fillStyle = tg;
        ctx.fillRect(tpx - tr * 2, tpy - tr * 2, tr * 4, tr * 4);
      }
      // faint trailing smoke puff just behind the head.
      const spx = startX + (landX - startX) * Math.max(0, u - 0.05);
      const sBase = startY + (landY - startY) * Math.max(0, u - 0.05);
      const spy = sBase - apex * 4 * Math.max(0, u - 0.05) * (1 - Math.max(0, u - 0.05)) - 2;
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = `rgba(90, 78, 74, ${(0.10 * (1 - u)).toFixed(3)})`;
      ctx.beginPath();
      ctx.ellipse(spx, spy, bomb.size * 1.6, bomb.size * 1.2, 0, 0, Math.PI * 2);
      ctx.fill();
    }

    // the bomb itself: a hot additive glow rim, then a dark rock head.
    ctx.globalCompositeOperation = 'lighter';
    const hg = ctx.createRadialGradient(px, py, 0, px, py, bomb.size * 2.4);
    hg.addColorStop(0, `rgba(255, 235, 185, ${(0.7 * dayK).toFixed(3)})`);
    hg.addColorStop(0.5, `rgba(255, 130, 45, ${(0.45 * dayK).toFixed(3)})`);
    hg.addColorStop(1, 'rgba(255, 80, 20, 0)');
    ctx.fillStyle = hg;
    ctx.fillRect(px - bomb.size * 2.4, py - bomb.size * 2.4, bomb.size * 4.8, bomb.size * 4.8);
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = 'rgba(34, 16, 10, 0.95)';
    traceRock(px, py, bomb.size, bomb.rockVerts); ctx.fill();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = `rgba(255, 150, 60, ${(0.5 * dayK).toFixed(3)})`;
    ctx.lineWidth = 1;
    traceRock(px, py, bomb.size, bomb.rockVerts); ctx.stroke();

    // IMPACT FLASH + fading ember/scorch as it nears the ground (u > 0.9).
    if (!reduced && u > 0.9) {
      const fk = (u - 0.9) / 0.1;                    // 0 → 1 across the last leg
      const fa = (1 - Math.abs(fk - 0.5) * 2);       // peak mid-impact
      const fr = bomb.size * (3 + fk * 4);
      const fg = ctx.createRadialGradient(landX, landY, 0, landX, landY, fr);
      fg.addColorStop(0, `rgba(255, 245, 210, ${(0.8 * fa * dayK).toFixed(3)})`);
      fg.addColorStop(0.5, `rgba(255, 140, 50, ${(0.5 * fa * dayK).toFixed(3)})`);
      fg.addColorStop(1, 'rgba(255, 70, 20, 0)');
      ctx.fillStyle = fg;
      ctx.fillRect(landX - fr, landY - fr, fr * 2, fr * 2);
    }
  }
  ctx.restore();
}

/** Ember-fountain vents on the midground ridge — ballistic spark arcs, recycled. */
function drawEmberFountains(
  ctx: CanvasRenderingContext2D, w: number, h: number, t: number, vs: VolcanicScene, dc: DayCycle,
  wx: WeatherFx | null, ridgeYAt: (x: number) => number
): void {
  if (t === 0) return;                          // reduced motion: no active fountains
  // daytime floor: embers stay clearly visible at midday (0.7) rising toward full
  // at night — an active vent must not wash out in daylight.
  const dayK = 0.7 + 0.3 * (1 - dc.bright);
  const tier = wx ? wx.tierN : 0;
  const sparks = Math.round(8 + tier * 14);     // per vent, scales with weather
  const hMul = 1 + tier * 0.8;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let f = 0; f < vs.fountains.length; f++) {
    const fo = vs.fountains[f];
    const vx0 = w * fo.xFrac;
    const vy0 = ridgeYAt(vx0) + 2;
    for (let s = 0; s < sparks; s++) {
      // recycle each spark over a ~1.6s ballistic life, offset per spark.
      const life = ((t * 0.9 + s * 0.137 + fo.phase) % 1.6) / 1.6;
      const vy = fo.vy * hMul;
      const px = vx0 + (fo.vx + (s % 5 - 2) * 4) * life;
      const py = vy0 - (vy * life - 0.5 * 90 * life * life);  // up then gravity
      const a = (1 - life) * dayK * (0.6 + (1 - dc.bright) * 0.4);
      if (a <= 0.02) continue;
      ctx.globalAlpha = a;
      ctx.fillStyle = life < 0.4 ? 'rgba(255, 220, 150, 1)' : 'rgba(255, 110, 40, 1)';
      const r = 1.4 - life;
      ctx.beginPath(); ctx.arc(px, py, Math.max(0.6, r), 0, Math.PI * 2); ctx.fill();
    }
  }
  ctx.restore();
}

/** Orange-red branching volcanic lightning restricted to the ash-plume column. */
function drawVolcLightning(ctx: CanvasRenderingContext2D, w: number, h: number, t: number, vs: VolcanicScene, horizonY: number): void {
  const cyc = t % 3.8;
  if (cyc > 0.16) return;                        // brief strike
  const fl = Math.max(0, 1 - cyc / 0.16);
  const cx = w * vs.cone.cxFrac;
  const topY = horizonY - h * vs.cone.peakFrac - h * 0.18;
  // deterministic jagged polyline down the plume column (seeded by the strike cycle)
  const seed = Math.floor(t / 3.8) * 911 + 7;
  const jr = splitmix32(seed >>> 0 || 1);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const pts: { x: number; y: number }[] = [{ x: cx + (jr() - 0.5) * w * 0.05, y: topY }];
  const segs = 6;
  for (let s = 1; s <= segs; s++) {
    const py = topY + (horizonY - h * vs.cone.peakFrac - topY) * (s / segs);
    pts.push({ x: cx + (jr() - 0.5) * w * 0.07, y: py });
  }
  // wide low-alpha glow stroke
  ctx.strokeStyle = `rgba(255, 90, 30, ${(0.35 * fl).toFixed(3)})`;
  ctx.lineWidth = 6;
  ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
  for (const p of pts) ctx.lineTo(p.x, p.y);
  ctx.stroke();
  // bright jagged core
  ctx.strokeStyle = `rgba(255, 210, 150, ${(0.85 * fl).toFixed(3)})`;
  ctx.lineWidth = 1.4;
  ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
  for (const p of pts) ctx.lineTo(p.x, p.y);
  ctx.stroke();
  ctx.restore();
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
function reflWaveRGB(cache: LandedCache, sunUp: boolean): string {
  if (sunUp) {
    const { r, g, b } = cache.sc;
    return `${Math.min(255, r + 30)}, ${Math.min(255, g + 50)}, ${Math.min(255, b + 60)}`;
  }
  return '150, 185, 210';
}

/** Draw the SIBLING PLANETS as distant discs in the sky — a small, dimmed echo of
 *  the flight scene's per-kind treatment (gas-giant banding, ice pale, volcanic
 *  ember, rings). Position/colours are cached; only a faint cosmetic shimmer of the
 *  rim varies per frame. Subtle by design so it never overpowers the HUD. */
function drawLandedSkyPlanets(
  ctx: CanvasRenderingContext2D, w: number, horizonY: number,
  t: number, cache: LandedCache, dc: DayCycle
): void {
  for (let i = 0; i < cache.skyPlanets.length; i++) {
    const p = cache.skyPlanets[i];
    // arc position this frame; skip when below the horizon.
    const pos = bodyArcPos(t, p.arcRate, p.arcOffset, p.arcDir, w, horizonY);
    if (!pos.up) continue;
    const px = pos.x, py = pos.y;
    // alpha: base distance dim × horizon extinction × day-cycle prominence (still
    // faintly visible by day, prominent at night).
    const a = p.alpha * pos.fade * (0.45 + dc.bodyBright * 0.85);
    if (a <= 0.01) continue;
    ctx.save();
    ctx.globalAlpha = a;
    // body disc (clipped) — base fill + a couple of cheap "band" arcs for character
    ctx.save();
    ctx.beginPath();
    ctx.arc(px, py, p.r, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = p.baseColor;
    ctx.fillRect(px - p.r, py - p.r, p.r * 2, p.r * 2);
    ctx.fillStyle = p.bandColor;
    if (p.treatment === 'GAS_GIANT') {
      for (let b = -2; b <= 2; b++) {
        ctx.fillRect(px - p.r, py + b * p.r * 0.4 - p.r * 0.12, p.r * 2, p.r * 0.24);
      }
    } else if (p.treatment === 'VOLCANIC') {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = p.bandColor;
      ctx.fillRect(px - p.r * 0.6, py - p.r * 0.2, p.r * 0.5, p.r * 0.4);
      ctx.fillRect(px + p.r * 0.1, py + p.r * 0.1, p.r * 0.4, p.r * 0.3);
      ctx.restore();
    } else if (p.treatment !== 'BARREN' && p.treatment !== 'MOUNTAINOUS') {
      ctx.fillRect(px - p.r, py - p.r * 0.1, p.r * 2, p.r * 0.5);
    }
    // FULLY-LIT distant disc — a faraway planet shows no crescent/terminator
    // (phases are only for nearby moons). Only a soft spherical sheen, no dark
    // limb that would read as a phase.
    const lg = ctx.createRadialGradient(px - p.r * 0.3, py - p.r * 0.3, p.r * 0.1, px, py, p.r * 1.2);
    lg.addColorStop(0, 'rgba(255,255,255,0.16)');
    lg.addColorStop(0.7, 'rgba(255,255,255,0)');
    lg.addColorStop(1, 'rgba(0,0,0,0.1)'); // barely-there rim, not a terminator
    ctx.fillStyle = lg;
    ctx.fillRect(px - p.r, py - p.r, p.r * 2, p.r * 2);
    ctx.restore();
    // faint rim shimmer (cosmetic; calm at t=0)
    const shimmer = t === 0 ? 0.5 : 0.4 + 0.2 * Math.sin(t * 0.4 + i);
    ctx.strokeStyle = p.rimColor;
    ctx.globalAlpha = a * shimmer;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(px, py, p.r + 0.5, 0, Math.PI * 2);
    ctx.stroke();
    if (p.rings) {
      ctx.globalAlpha = a * 0.7;
      ctx.strokeStyle = p.rimColor;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(px, py, p.r * 1.8, p.r * 0.5, -0.3, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }
}

/** Draw the MOONS, each ARCING across the sky on the day cycle (rise/cross/set).
 *  The terminator is PHYSICAL: every moon is lit from the actual sun direction
 *  (sunWorldX/Y) and its phase AMOUNT follows its angular separation from the sun
 *  (near the sun ⇒ thin crescent/new, opposite ⇒ full) — so all moons are lit the
 *  same way and the phases evolve as the sun + moons arc. Prominent at night, faint
 *  by day (dc.bodyBright), faded near the horizon. */
function drawLandedMoons(
  ctx: CanvasRenderingContext2D, w: number, horizonY: number,
  t: number, cache: LandedCache, dc: DayCycle,
  sunWorldX: number, sunWorldY: number, sunAlt: number, sunAzFrac: number
): void {
  const prom = dc.bodyBright;  // moons prominent at night, faint by day
  const sunVec = skyDir(sunAlt, sunAzFrac);
  // ONE global PARALLEL sun-light direction shared by ALL moons. The sun is
  // effectively at infinity vs moon-to-moon distances, so every moon's lit limb
  // faces the SAME screen direction (parallel rays) — never each toward the sun's
  // finite screen point (that made adjacent moons' terminators diverge). Anchor the
  // direction at the scene's horizon-centre so it points consistently toward the sun
  // and rotates slowly as the sun arcs; computed from the sun's TRUE position so it
  // stays correct even when the sun is below the horizon (night/twilight).
  const lightDir = Math.atan2(sunWorldY - horizonY, sunWorldX - w / 2);
  const lightCos = Math.cos(lightDir), lightSin = Math.sin(lightDir);
  for (let i = 0; i < cache.moons.length; i++) {
    const m = cache.moons[i];
    // arc position this frame; skip when below the horizon.
    const pos = bodyArcPos(t, m.arcRate, m.arcOffset, m.arcDir, w, horizonY);
    if (!pos.up) continue;
    const mx = pos.x, my = pos.y;
    const ext = pos.fade;        // atmospheric extinction near the horizon
    const breathe = t === 0 ? 1 : 0.96 + 0.04 * Math.sin(t * 0.3 + i);

    // PHYSICAL phase from the TRUE angular separation between moon and sun:
    // illum = (1 - cos(sep)) / 2 → 0 at the sun (NEW), 0.5 at 90°, 1 opposite (FULL).
    const moonVec = skyDir(pos.alt, pos.azFrac);
    const cosSep = Math.max(-1, Math.min(1,
      sunVec.x * moonVec.x + sunVec.y * moonVec.y + sunVec.z * moonVec.z));
    const illum = (1 - cosSep) / 2;
    // near-the-sun wash-out: a moon hugging the bright sun reads faint (and is
    // near-new anyway) so we never paste a prominent moon beside the sun.
    const nearSun = Math.max(0, 1 - illum * 2.2);       // ~1 when very near the sun
    const sunWash = 1 - nearSun * 0.75;
    // DAYTIME moon: while the sun is UP a moon is a faint, pale, FULLY-LIT disc —
    // no carved terminator (a shadowed moon beside a visible sun reads as wrong).
    // The proper sun-lit phase is only shown at night/twilight (sun at/below horizon).
    const dayMoon = dc.sunUp;
    const dayFaint = dayMoon ? 0.4 : 1;                 // washed-out by daylight

    ctx.save();
    // soft halo (additive) — gradient rebuilt per frame at the live position.
    ctx.globalCompositeOperation = 'lighter';
    const halo = ctx.createRadialGradient(mx, my, m.r * 0.6, mx, my, m.r * 2.6);
    halo.addColorStop(0, `rgba(${m.tint}, ${(0.18 * prom * ext * sunWash * dayFaint).toFixed(3)})`);
    halo.addColorStop(1, `rgba(${m.tint}, 0)`);
    ctx.fillStyle = halo;
    ctx.fillRect(mx - m.r * 2.6, my - m.r * 2.6, m.r * 5.2, m.r * 5.2);
    ctx.restore();

    // lit disc
    ctx.save();
    ctx.globalAlpha = (0.5 + 0.5 * prom) * breathe * ext * sunWash * dayFaint;
    ctx.fillStyle = `rgb(${m.tint})`;
    ctx.beginPath();
    ctx.arc(mx, my, m.r, 0, Math.PI * 2);
    ctx.fill();
    // faint mare mottling for texture (cheap, two darker blobs; tint precomputed)
    ctx.globalAlpha *= 0.4;
    ctx.fillStyle = `rgba(${m.mareTint}, 1)`;
    ctx.beginPath();
    ctx.arc(mx - m.r * 0.3, my - m.r * 0.2, m.r * 0.28, 0, Math.PI * 2);
    ctx.arc(mx + m.r * 0.25, my + m.r * 0.3, m.r * 0.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // terminator — ONLY at night/twilight (sun down). Carve the unlit portion with
    // a shadow disc offset along the SHARED parallel lightDir (toward the sun), so
    // EVERY moon's lit limb faces the same way (parallel rays from a distant sun) —
    // adjacent moons never show mismatched shadow angles. Daytime → full pale discs.
    if (!dayMoon && illum < 0.985) {
      ctx.save();
      ctx.globalAlpha = ext * sunWash;
      ctx.beginPath();
      ctx.arc(mx, my, m.r, 0, Math.PI * 2);
      ctx.clip();
      const k = (illum - 0.5) * 2; // -1 (new) … +1 (full)
      // shadow cast AWAY from the sun: opposite the shared lightDir, same for all.
      const dx = -lightCos * m.r * k;
      const dy = -lightSin * m.r * k;
      const sr = m.r * (1.0 + (1 - Math.abs(k)) * 0.04);
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = 'rgba(8, 10, 18, 0.82)';
      ctx.beginPath();
      ctx.arc(mx + dx, my + dy, sr, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    // NOTE: moons never carry rings — rings render only on the distant sky-PLANETS
    // (full-lit, unphased discs). A ringed-AND-phased body would read as wrong.
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
    // embers hug the lava fissures/cracks: anchored near a vent x, short rise, fire-
    // orange. Now visible GLOWING motes (soft arc + halo, not 1px). Day ×0.5/night ×1.
    ctx.globalCompositeOperation = 'lighter';
    const fiss = cache.volcFissures;
    const dayBright = dayCycleAt(t, cache.dayPhaseOffset).bright;
    const dayK = dayBright > 0.5 ? 0.5 : 1.0;
    const RISE = h * 0.22; // short rise — embers stay low near the fire
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const anchorX = fiss.length > 0 ? fiss[i % fiss.length].x : (p.x % w);
      const x = anchorX + Math.sin(t * 1.1 + p.phase) * 7 + p.drift * 3;
      const rise = ((p.y % RISE) + t * (10 + p.speed * 16) * (0.7 + pMul * 0.3)) % RISE;
      const y = (cache.horizonY + RISE) - rise;
      const flick = 0.4 + 0.6 * Math.abs(Math.sin(t * 3 + p.phase));
      const a = Math.min(1, 0.6 * flick * (1 - rise / RISE) * pMul * dayK);
      const r = (1.1 + p.size * 1.1) * (1 - rise / RISE * 0.4);  // bigger, shrinks as it rises
      // soft halo
      ctx.globalAlpha = a * 0.5;
      const hg = ctx.createRadialGradient(x, y, 0, x, y, r * 2.4);
      hg.addColorStop(0, p.warm > 0.5 ? 'rgba(255, 170, 70, 1)' : 'rgba(255, 100, 35, 1)');
      hg.addColorStop(1, 'rgba(255, 60, 10, 0)');
      ctx.fillStyle = hg;
      ctx.fillRect(x - r * 2.4, y - r * 2.4, r * 4.8, r * 4.8);
      // bright core
      ctx.globalAlpha = a;
      ctx.fillStyle = p.warm > 0.5 ? 'rgba(255, 210, 140, 1)' : 'rgba(255, 130, 60, 1)';
      ctx.beginPath(); ctx.arc(x, y, Math.max(0.8, r * 0.5), 0, Math.PI * 2); ctx.fill();
    }
  } else if (kind === 'SNOW') {
    // SNOW — a PERSISTENT light flurry on every ice world even on a CALM day,
    // intensifying with weather. pMul has a CALM floor so snow always reads; the
    // fall speed + alpha grow with the tier toward a full blizzard.
    const snowMul = Math.max(1.0, pMul);    // never below the calm baseline
    ctx.fillStyle = 'rgba(235, 246, 255, 1)';
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const fall = (p.y + t * (12 + p.speed * 16) * (0.8 + snowMul * 0.3)) % h;
      // wind-driven sideways drift grows with the tier (blizzard slants the snow).
      const wind = p.drift * t * (2 + (snowMul - 1) * 6);
      const x = (((p.x + Math.sin(t * 0.6 + p.phase) * 12 + wind) % w) + w) % w;
      ctx.globalAlpha = Math.min(1, (0.45 + p.warm * 0.4) * snowMul);
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
/** Per-biome architectural vocabulary: palette (lighter than the ridge), window
 *  glow colour, the signature landmark kind, and the filler kinds the cluster is
 *  built from. Deterministic lookup. */
function biomeArch(flourish: LandedPalette['flourish'], pal: LandedPalette): {
  body: string; bodyLight: string; bodyDark: string;
  winGlow: { r: number; g: number; b: number }; accent: string;
  signature: CitadelKind; fillers: CitadelKind[]; beaconKind: 'AIRCRAFT' | 'LAMP' | 'LAVA_PLUME';
} {
  const ridge = hexToRgb(pal.ridges[2] || pal.ridges[0] || '#5a5560');
  const lift = (k: number) => ({
    r: Math.min(255, Math.round(ridge.r + (255 - ridge.r) * k)),
    g: Math.min(255, Math.round(ridge.g + (255 - ridge.g) * k)),
    b: Math.min(255, Math.round(ridge.b + (255 - ridge.b) * k)),
  });
  const rgb = (c: { r: number; g: number; b: number }) => `rgb(${c.r}, ${c.g}, ${c.b})`;
  // base: a touch lighter than the ridge so the city reads as built, not terrain.
  const baseC = lift(0.22), lightC = lift(0.4), darkC = { r: Math.round(ridge.r * 0.7), g: Math.round(ridge.g * 0.7), b: Math.round(ridge.b * 0.72) };
  switch (flourish) {
    case 'VOLCANIC':
      // forge-city: bodies lifted a touch for contrast against the dark terrain;
      // warm accents; new heat-forge structure vocabulary; lava-plume beacon.
      return { body: 'rgb(58, 48, 52)', bodyLight: 'rgb(92, 76, 78)', bodyDark: 'rgb(30, 24, 28)',
        winGlow: { r: 255, g: 130, b: 60 }, accent: 'rgba(255, 110, 40, 1)',
        signature: 'SMELTER_CRUCIBLE',
        fillers: ['HEAT_VENT_TOWER', 'BLAST_FURNACE_DOME', 'OBSIDIAN_BUNKER', 'MAGMA_PIPELINE'],
        beaconKind: 'LAVA_PLUME' };
    case 'ICE':
      return { body: 'rgb(200, 224, 238)', bodyLight: 'rgb(228, 244, 252)', bodyDark: 'rgb(150, 180, 205)',
        winGlow: { r: 170, g: 230, b: 255 }, accent: 'rgba(210, 245, 255, 1)',
        signature: 'ICEDOME', fillers: ['SPIRE', 'DOME', 'BOX'], beaconKind: 'AIRCRAFT' };
    case 'DESERT':
      return { body: 'rgb(196, 158, 110)', bodyLight: 'rgb(222, 188, 142)', bodyDark: 'rgb(150, 116, 78)',
        winGlow: { r: 255, g: 190, b: 110 }, accent: 'rgba(235, 205, 150, 1)',
        signature: 'ZIGGURAT', fillers: ['DOME', 'WINDTOWER', 'BOX'], beaconKind: 'AIRCRAFT' };
    case 'OCEANIC':
      // coastal-future colony: clean WHITE bodies, sea-blue shadow face, warm-lit
      // windows, a TEAL accent trim. Bespoke maritime vocabulary (see drawCitadelStruct).
      return { body: 'rgb(228, 238, 244)', bodyLight: 'rgb(248, 252, 255)', bodyDark: 'rgb(150, 178, 196)',
        winGlow: { r: 255, g: 226, b: 168 }, accent: 'rgba(40, 200, 190, 1)',
        signature: 'LIGHTHOUSE', fillers: ['SEADOME', 'STILT_PLATFORM', 'DESAL_TOWER', 'SAIL_HALL'], beaconKind: 'LAMP' };
    case 'TERRAN':
      return { body: 'rgb(150, 190, 165)', bodyLight: 'rgb(190, 222, 198)', bodyDark: 'rgb(96, 140, 116)',
        winGlow: { r: 210, g: 255, b: 200 }, accent: 'rgba(150, 235, 170, 1)',
        signature: 'ARCOLOGY', fillers: ['DOME', 'BOX', 'ARCOLOGY'], beaconKind: 'AIRCRAFT' };
    case 'MOUNTAINOUS':
      return { body: rgb(baseC), bodyLight: rgb(lightC), bodyDark: rgb(darkC),
        winGlow: { r: 255, g: 200, b: 130 }, accent: 'rgba(220, 215, 205, 1)',
        signature: 'KEEP', fillers: ['BATTLEMENT', 'BOX', 'BATTLEMENT'], beaconKind: 'AIRCRAFT' };
    default: // NONE → barren / gas / unknown: utilitarian steel habitat.
      return { body: 'rgb(150, 156, 168)', bodyLight: 'rgb(186, 192, 204)', bodyDark: 'rgb(96, 102, 116)',
        winGlow: { r: 200, g: 215, b: 235 }, accent: 'rgba(200, 210, 225, 1)',
        signature: 'GEODESIC', fillers: ['DOME', 'TANK', 'BOX'], beaconKind: 'AIRCRAFT' };
  }
}

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
  if (level <= 0) return null;
  const arch = biomeArch(pal.flourish, pal);
  const rng = splitmix32(seed * 1009 + level * 17);
  // land bounds the whole skyline (cityX, spread, every structure x, the beacon).
  const lMin = landX ? Math.max(8, landX.min) : 8;
  const lMax = landX ? Math.min(w - 8, landX.max) : w - 8;
  const clampX = (x: number) => Math.max(lMin, Math.min(lMax, x));

  // Level progression: count, footprint width, max height + window density grow
  // with the tier. 1 Outpost → 5 Planetary Capital.
  const cfg = [
    { n: 0, maxH: 0,    win: 0,   sigH: 0,    spreadF: 0    },
    { n: 1, maxH: 0.05, win: 0,   sigH: 0,    spreadF: 0.06 }, // Outpost
    { n: 4, maxH: 0.07, win: 0.1, sigH: 0,    spreadF: 0.14 }, // Settlement
    { n: 6, maxH: 0.10, win: 0.25, sigH: 0.14, spreadF: 0.22 }, // Colony (1 landmark)
    { n: 9, maxH: 0.15, win: 0.45, sigH: 0.20, spreadF: 0.32 }, // Major Colony
    { n: 14, maxH: 0.20, win: 0.7, sigH: 0.30, spreadF: 0.46 }, // Planetary Capital
  ][level];
  if (!cfg || cfg.n === 0) return null;

  // VOLCANIC forge-cities read a touch larger (player request): a GENTLE +8% bump to
  // structure footprints + heights (a touch bigger than stock, not oversized — 1.2
  // looked too big at L1). Grounding is unaffected — heights grow upward from the
  // same ground baseline. Other biomes unchanged.
  const isVolcanic = pal.flourish === 'VOLCANIC';
  const isOceanic = pal.flourish === 'OCEANIC';
  const sizeMul = isVolcanic ? 1.08 : 1.0;

  const cityX = clampX(lMin + (lMax - lMin) * (0.3 + rng() * 0.4));
  const spread = Math.min(w * cfg.spreadF, (lMax - lMin) * 0.5);

  const buildWindows = (bw: number, bh: number, density: number) => {
    const windows: { dx: number; dy: number; warm: number }[] = [];
    if (density <= 0) return windows;
    const rows = Math.max(1, Math.floor(bh / 7));
    const cols = Math.max(1, Math.round(bw / 5));
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (rng() > density) continue;
        const dx = (c - (cols - 1) / 2) * (bw / Math.max(1, cols)) * 0.9;
        windows.push({ dx, dy: 5 + r * 7, warm: rng() });
      }
    }
    return windows;
  };

  const structures: CitadelStructure[] = [];

  // SIGNATURE landmark (level ≥ 3): the dominant central structure at cityX.
  if (level >= 3 && cfg.sigH > 0) {
    const sbw = Math.max(8, w * (0.03 + level * 0.006)) * sizeMul;
    const sbh = h * cfg.sigH * (0.8 + rng() * 0.3) * sizeMul;
    structures.push({
      kind: arch.signature, x: cityX, bw: sbw, bh: sbh,
      windows: buildWindows(sbw, sbh, cfg.win), signature: true,
      v1: rng(), v2: rng(),
    });
  }

  // VOLCANIC filler pool: MAGMA_PIPELINE only at L3+ (a connector implies a complex);
  // CATWALK_GANTRY added at L4+. Other biomes use arch.fillers as-is.
  let fillerPool = arch.fillers;
  if (isVolcanic) {
    fillerPool = arch.fillers.filter((k) => k !== 'MAGMA_PIPELINE' || level >= 3);
    if (level >= 4) fillerPool = [...fillerPool, 'CATWALK_GANTRY'];
  }

  // FILLER cluster around the centre (the remaining count).
  const fillN = Math.max(0, cfg.n - (structures.length));
  for (let i = 0; i < fillN; i++) {
    const jitter = (rng() - 0.5) * 2 * spread;
    const sx = clampX(cityX + jitter);
    const bw = Math.max(4, w * (0.012 + rng() * 0.022)) * sizeMul;
    // height falls off with distance from centre so the skyline reads as a city.
    const distF = 1 - Math.min(1, Math.abs(sx - cityX) / Math.max(1, spread)) * 0.45;
    const bh = h * cfg.maxH * (0.35 + rng() * 0.65) * distF * sizeMul;
    // L1 is a single humble outpost (volcanic → a heat-vent tower, not a plain box);
    // L2 leads with a crucible/furnace so even a small base reads forge-industrial.
    let kind: CitadelKind;
    if (level === 1) {
      kind = isVolcanic ? 'HEAT_VENT_TOWER' : isOceanic ? 'STILT_PLATFORM' : 'BOX';
    } else if (isVolcanic && level === 2 && i === 0) {
      kind = rng() < 0.5 ? 'SMELTER_CRUCIBLE' : 'BLAST_FURNACE_DOME';
    } else {
      kind = fillerPool[Math.floor(rng() * fillerPool.length)];
    }
    structures.push({
      kind, x: sx, bw, bh, windows: buildWindows(bw, bh, cfg.win),
      signature: false, v1: rng(), v2: rng(),
    });
  }

  // L1 Outpost: append a slender antenna mast beside the lone structure.
  if (level === 1 && structures.length > 0) {
    const base = structures[0];
    structures.push({
      kind: 'MAST', x: clampX(base.x + base.bw * 1.2), bw: Math.max(2, base.bw * 0.25),
      bh: base.bh * 1.6, windows: [], signature: false, v1: rng(), v2: rng(),
    });
  }

  // VOLCANIC + OCEANIC: re-lay the row with EVEN, NON-OVERLAPPING spacing. The random
  // jitter above packs structures into ±spread (and on a side-window biome the room is
  // narrow), so footprints (esp. wide domes/sail halls) intersected. Distribute across
  // the FULL window and enforce a center-to-center gap = effHalf[i]+effHalf[i+1]+pad,
  // using each kind's ACTUAL rendered half-width (domes/halls extend past bw).
  if ((isVolcanic || isOceanic) && structures.length > 0) {
    // effective half-footprint per kind (mirrors clipStructPath's widest extent).
    const effHalf = (st: CitadelStructure): number => {
      const half = st.bw / 2;
      switch (st.kind) {
        case 'DOME': return half * 1.1;
        case 'GEODESIC': return Math.max(half * 1.4, st.bh * 0.55);
        case 'ICEDOME': return Math.max(half * 1.4, st.bh * 0.5);
        case 'BLAST_FURNACE_DOME': return Math.max(half, st.bh * 0.9);
        case 'SEADOME': return half * 1.15;             // glass dome arcs past bw
        case 'SAIL_HALL': return half * 1.12;           // peaked roof overhangs a touch
        case 'STILT_PLATFORM': return half * 1.18;      // deck cantilevers past the legs
        case 'MAST': return Math.max(2, half);
        default: return half;     // upright bodies + tapered towers ≈ bw/2
      }
    };
    // order the row so the SIGNATURE/largest sits in the MIDDLE: sort by effHalf
    // descending, then deal alternately outward from centre (big core, smaller wings).
    const ordered = [...structures].sort((a, b) => effHalf(b) - effHalf(a));
    const arranged: CitadelStructure[] = [];
    for (let i = 0; i < ordered.length; i++) {
      if (i % 2 === 0) arranged.push(ordered[i]);      // grow to the right end
      else arranged.unshift(ordered[i]);               // grow to the left end
    }
    const winW = lMax - lMin;
    const gaps = Math.max(0, arranged.length - 1);
    const footSum = arranged.reduce((s2, st) => s2 + effHalf(st) * 2, 0);
    // choose the pad (gap) so the row fits the window: start from a comfortable gap,
    // squeeze it toward 0 if needed. Footprints stay intact (no overlap) unless even
    // touching footprints overflow — then scale every footprint down proportionally.
    const wantPad = Math.max(3, w * 0.006);
    let pad = wantPad;
    if (footSum + wantPad * gaps > winW) {
      const avail = winW - footSum;                    // room left for the gaps
      pad = gaps > 0 ? Math.max(0, avail / gaps) : 0;  // squeeze pads first
      if (avail < 0) {                                 // footprints alone overflow → scale
        const scale = winW / footSum;
        for (const st of arranged) { st.bw *= scale; st.bh *= scale; }
        pad = 0;
      }
    }
    // recompute total with the chosen pad + (possibly scaled) footprints, then centre
    // the row in the window and walk left→right so adjacent footprints never intersect.
    const totalW = arranged.reduce((s2, st) => s2 + effHalf(st) * 2, 0) + pad * gaps;
    let cursor = lMin + Math.max(0, (winW - totalW) / 2);
    for (let i = 0; i < arranged.length; i++) {
      const eh = effHalf(arranged[i]);
      cursor += eh;
      arranged[i].x = clampX(cursor);
      cursor += eh + pad;
    }
    return finalizeLayout();
  }

  return finalizeLayout();

  function finalizeLayout(): CitadelLayout {
    return {
      structures,
      beacon: level >= 5 || (arch.beaconKind === 'LAMP' && level >= 3),
      beaconKind: arch.beaconKind,
      cityX, maxH: cfg.maxH,
      twinkleWindows: level >= 4,
      biome: pal.flourish,
      body: arch.body, bodyLight: arch.bodyLight, bodyDark: arch.bodyDark,
      winGlow: arch.winGlow, accent: arch.accent,
    };
  }
}

/** Trace a structure's SILHOUETTE as the current path so window lights can be
 *  clipped to the actual body shape (not a bounding rectangle). Mirrors the
 *  geometry in drawCitadelStruct. Caller does beginPath()→this→clip(). */
function clipStructPath(
  ctx: CanvasRenderingContext2D, st: CitadelStructure, groundY: number
): void {
  const { x, bw, bh } = st;
  const half = bw / 2;
  const topY = groundY - bh;
  switch (st.kind) {
    case 'DOME': {
      const r = half * 1.1;
      const cy = groundY - r * 0.5;
      ctx.rect(x - bw / 2, cy, bw, r * 0.5);   // base box
      ctx.moveTo(x + r, cy); ctx.arc(x, cy, r, 0, Math.PI, true); // dome
      break;
    }
    case 'GEODESIC':
    case 'ICEDOME': {
      const r = st.kind === 'ICEDOME' ? Math.max(half * 1.4, bh * 0.5) : Math.max(half * 1.4, bh * 0.55);
      ctx.moveTo(x + r, groundY); ctx.arc(x, groundY, r, 0, Math.PI, true);
      break;
    }
    case 'TANK': {
      const yT = topY + bw * 0.3;
      ctx.rect(x - half, yT, bw, groundY - yT);
      ctx.moveTo(x + half, yT); ctx.ellipse(x, yT, half, bw * 0.3, 0, 0, Math.PI, true);
      break;
    }
    case 'ZIGGURAT': {
      const steps = 3 + Math.floor(st.v1 * 3);
      const stepH = bh / steps;
      for (let s2 = 0; s2 < steps; s2++) {
        const ww = bw * (1 - s2 / (steps + 1));
        ctx.rect(x - ww / 2, groundY - (s2 + 1) * stepH, ww, stepH);
      }
      break;
    }
    case 'ARCOLOGY': {
      const tiers = 3 + Math.floor(st.v1 * 2);
      const tierH = bh / tiers;
      for (let t2 = 0; t2 < tiers; t2++) {
        const ww = bw * (1 - t2 * 0.12);
        ctx.rect(x - ww / 2, groundY - (t2 + 1) * tierH, ww, tierH);
      }
      break;
    }
    case 'SPIRE': {
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - half * 0.4, topY); ctx.lineTo(x, topY - bh * 0.12);
      ctx.lineTo(x + half * 0.4, topY); ctx.lineTo(x + half, groundY); ctx.closePath();
      break;
    }
    case 'LIGHTHOUSE': {
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - half * 0.5, topY + bh * 0.12);
      ctx.lineTo(x + half * 0.5, topY + bh * 0.12); ctx.lineTo(x + half, groundY); ctx.closePath();
      break;
    }
    case 'WINDTOWER':
      ctx.rect(x - bw * 0.3, topY, bw * 0.6, bh); break;
    case 'SMELTER_CRUCIBLE': {
      // tapered tower: wider at the base (bw) narrowing to ~0.62 at the top.
      const tw = bw * 0.62;
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - tw / 2, topY);
      ctx.lineTo(x + tw / 2, topY); ctx.lineTo(x + half, groundY); ctx.closePath();
      break;
    }
    case 'BLAST_FURNACE_DOME': {
      const r = Math.max(half, bh * 0.9);
      ctx.moveTo(x + r, groundY); ctx.arc(x, groundY, r, 0, Math.PI, true);
      break;
    }
    case 'HEAT_VENT_TOWER':
      ctx.rect(x - bw * 0.28, topY, bw * 0.56, bh); break;
    case 'OBSIDIAN_BUNKER': {
      // parallelogram with inward-sloped walls (wider base, narrower top).
      const inset = bw * 0.16;
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - half + inset, topY);
      ctx.lineTo(x + half - inset, topY); ctx.lineTo(x + half, groundY); ctx.closePath();
      break;
    }
    case 'MAGMA_PIPELINE': {
      // two pillars (no windows really, but clip to their union just in case).
      ctx.rect(x - half, topY, bw * 0.22, bh);
      ctx.rect(x + half - bw * 0.22, topY, bw * 0.22, bh);
      break;
    }
    case 'CATWALK_GANTRY':
      ctx.rect(x - bw * 0.18, topY, bw * 0.36, bh); break;
    // --- OCEANIC coastal-future kinds (pass 3) ---
    case 'SEADOME': {
      const r = half * 1.15;
      const cy = groundY - r * 0.4;
      ctx.rect(x - bw / 2, cy, bw, r * 0.4);             // base ring
      ctx.moveTo(x + r, cy); ctx.arc(x, cy, r, 0, Math.PI, true);
      break;
    }
    case 'DESAL_TOWER': {
      const cw = bw * 0.66;
      const tankH = bh * 0.16;
      const bodyTop = topY + tankH;
      ctx.rect(x - cw / 2, bodyTop, cw, groundY - bodyTop);
      ctx.moveTo(x + cw * 0.62, bodyTop); ctx.ellipse(x, bodyTop, cw * 0.62, tankH, 0, 0, Math.PI, true);
      break;
    }
    case 'STILT_PLATFORM': {
      const deckH = Math.max(4, bh * 0.34);
      const deckTop = groundY - bh;
      const deckW = bw * 1.15;
      ctx.rect(x - deckW / 2, deckTop, deckW, deckH);    // window-clip to the deck slab
      break;
    }
    case 'SAIL_HALL': {
      const wallH = bh * 0.32;
      const wallTop = groundY - wallH;
      const midY = topY + bh * 0.22;
      ctx.rect(x - half, wallTop, bw, wallH);            // base wall
      ctx.moveTo(x - half, wallTop);
      ctx.quadraticCurveTo(x - half * 0.5, midY, x, topY);
      ctx.quadraticCurveTo(x + half * 0.5, midY, x + half, wallTop);
      ctx.closePath();
      break;
    }
    default: // BOX/STILT/KEEP/BATTLEMENT/FOUNDRY/SMOKESTACK/MAST → upright body box
      ctx.rect(x - half, topY, bw, bh);
  }
}

/** The building FLOOR for a structure on uneven terrain: sample the ground across
 *  the full footprint [x−w/2 … x+w/2] and return the HIGHEST point (min Y) so the
 *  base never hangs over a drop on its low side (a foundation skirt then fills the
 *  gap down to the real terrain). 5 samples — cheap + robust on any slope. */
function footprintFloorY(st: CitadelStructure, ridgeYAt: (x: number) => number): number {
  const half = st.bw / 2;
  let minY = Infinity;
  for (let s = 0; s <= 4; s++) {
    const sx = st.x - half + (st.bw * s) / 4;
    const gy = ridgeYAt(sx);
    if (gy < minY) minY = gy;     // smaller y = higher ground
  }
  return minY === Infinity ? ridgeYAt(st.x) : minY;
}

/** Paint one biome-vocabulary structure. floorY is the foundation floor (highest
 *  ground under the footprint); a poured foundation fills from it down to the real
 *  terrain across the width so the base meets the ground along its entire span.
 *  Bodies use a left/right face pair for a consistent key-light. */
function drawCitadelStruct(
  ctx: CanvasRenderingContext2D,
  st: CitadelStructure, groundY: number, layout: CitadelLayout, faceSign: number,
  ridgeYAt: (x: number) => number, t = 0, winNight = 0.5
): void {
  const { x, bw, bh } = st;
  const topY = groundY - bh;
  const half = bw / 2;
  const lit = faceSign >= 0 ? layout.bodyLight : layout.bodyDark;
  const shade = faceSign >= 0 ? layout.bodyDark : layout.bodyLight;
  const volc = layout.biome === 'VOLCANIC';

  // FOUNDATION SKIRT — a solid poured base filling the gap from the flat building
  // floor (groundY = the highest ground under the footprint) DOWN to the actual
  // terrain surface across the full width. Guarantees no overhang/gap on the low
  // side; reads as a building founded on a slope. Generic for all kinds. Sampled
  // densely enough to follow the terrain; tinted a touch darker than the body.
  if (st.kind !== 'MAST' && st.kind !== 'STILT_PLATFORM') {
    let lowestY = groundY;
    ctx.save();
    ctx.fillStyle = layout.bodyDark;
    ctx.beginPath();
    ctx.moveTo(x - half, groundY);             // flat floor, left
    ctx.lineTo(x + half, groundY);             // flat floor, right
    // bottom edge follows the terrain right→left
    for (let sx = x + half; sx >= x - half; sx -= Math.max(3, bw / 6)) {
      const gy = ridgeYAt(sx) + 1;             // +1 so it meets, not floats above
      if (gy > lowestY) lowestY = gy;
      ctx.lineTo(sx, gy);
    }
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    // GROUND-CONTACT shadow at the foundation's actual lowest contact line — a soft
    // dark smudge, NOT a bright pad. Placed at the bottom of the skirt so it reads
    // as the building planted in the slope.
    ctx.save();
    const sw = bw * 0.62, sh = Math.max(1.5, bw * 0.12);
    const sg = ctx.createRadialGradient(x, lowestY, 0, x, lowestY, sw);
    // VOLCANIC: warm lava-tinted contact shadow (the ground glows from the lava).
    sg.addColorStop(0, volc ? 'rgba(80, 20, 5, 0.4)' : 'rgba(0, 0, 0, 0.34)');
    sg.addColorStop(1, volc ? 'rgba(80, 20, 5, 0)' : 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = sg;
    ctx.translate(x, lowestY);
    ctx.scale(1, sh / sw);
    ctx.translate(-x, -lowestY);
    ctx.fillRect(x - sw, lowestY - sw, sw * 2, sw * 2);
    ctx.restore();
  }

  const box = (yTop: number, hh: number, ww: number) => {
    ctx.fillStyle = layout.body;
    ctx.fillRect(x - ww / 2, yTop, ww, hh);
    // lit + shade faces split down the middle for a simple key-light read
    ctx.fillStyle = lit;
    ctx.fillRect(x, yTop, ww / 2, hh);
    ctx.fillStyle = shade;
    ctx.fillRect(x - ww / 2, yTop, ww / 2, hh);
  };

  switch (st.kind) {
    case 'BOX':
    case 'STILT': {
      box(topY, bh, bw);
      if (st.kind === 'STILT') {
        // stilt legs below into the shore/water line
        ctx.strokeStyle = layout.bodyDark;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x - half * 0.7, groundY); ctx.lineTo(x - half * 0.5, groundY + bh * 0.18);
        ctx.moveTo(x + half * 0.7, groundY); ctx.lineTo(x + half * 0.5, groundY + bh * 0.18);
        ctx.stroke();
      }
      break;
    }
    case 'TANK': {
      // squat rounded storage tank
      box(topY + bw * 0.3, bh - bw * 0.3, bw);
      ctx.fillStyle = lit;
      ctx.beginPath();
      ctx.ellipse(x, topY + bw * 0.3, half, bw * 0.3, 0, Math.PI, 0);
      ctx.fill();
      break;
    }
    case 'MAST': {
      ctx.strokeStyle = layout.bodyLight;
      ctx.lineWidth = Math.max(1, bw * 0.4);
      ctx.beginPath();
      ctx.moveTo(x, groundY); ctx.lineTo(x, topY);
      ctx.stroke();
      // cross-arms
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x - bw, topY + bh * 0.25); ctx.lineTo(x + bw, topY + bh * 0.25);
      ctx.stroke();
      break;
    }
    case 'DOME': {
      const r = half * 1.1;
      box(groundY - r * 0.5, r * 0.5, bw);
      ctx.fillStyle = layout.body;
      ctx.beginPath(); ctx.arc(x, groundY - r * 0.5, r, Math.PI, 0); ctx.fill();
      ctx.fillStyle = lit;
      ctx.beginPath(); ctx.arc(x, groundY - r * 0.5, r, Math.PI, Math.PI * 1.5); ctx.fill();
      break;
    }
    case 'GEODESIC': {
      // big faceted dome: arc body + triangular facet seams. Base flush on ground.
      const r = Math.max(half * 1.4, bh * 0.55);
      const cy = groundY;
      ctx.fillStyle = layout.body;
      ctx.beginPath(); ctx.arc(x, cy, r, Math.PI, 0); ctx.fill();
      ctx.fillStyle = lit;
      ctx.beginPath(); ctx.arc(x, cy, r, Math.PI, Math.PI * 1.5); ctx.fill();
      ctx.strokeStyle = layout.bodyDark; ctx.lineWidth = 1; ctx.globalAlpha = 0.5;
      const facets = 4 + Math.floor(st.v1 * 3);
      for (let f = 1; f < facets; f++) {
        const a = Math.PI + (f / facets) * Math.PI;
        ctx.beginPath(); ctx.moveTo(x, cy); ctx.lineTo(x + Math.cos(a) * r, cy + Math.sin(a) * r); ctx.stroke();
      }
      ctx.beginPath(); ctx.ellipse(x, cy, r * 0.6, r * 0.3, 0, Math.PI, 0); ctx.stroke();
      ctx.globalAlpha = 1;
      break;
    }
    case 'ICEDOME': {
      // faceted translucent ice dome + a crowning crystal spike. Base on ground.
      const r = Math.max(half * 1.4, bh * 0.5);
      const cy = groundY;
      ctx.fillStyle = layout.body; ctx.globalAlpha = 0.9;
      ctx.beginPath(); ctx.arc(x, cy, r, Math.PI, 0); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.fillStyle = lit;
      ctx.beginPath(); ctx.moveTo(x - r, cy); ctx.lineTo(x, cy - r); ctx.lineTo(x, cy); ctx.closePath(); ctx.fill();
      // crystal spike
      ctx.fillStyle = layout.accent;
      ctx.beginPath(); ctx.moveTo(x - half * 0.3, cy - r); ctx.lineTo(x, groundY - bh); ctx.lineTo(x + half * 0.3, cy - r); ctx.closePath(); ctx.fill();
      // sheen highlight
      ctx.strokeStyle = 'rgba(255,255,255,0.55)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, cy, r * 0.7, Math.PI * 1.15, Math.PI * 1.45); ctx.stroke();
      break;
    }
    case 'SPIRE': {
      // angular ice/crystal spire — a tall faceted shard
      ctx.fillStyle = layout.body;
      ctx.beginPath();
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - half * 0.4, topY); ctx.lineTo(x, topY - bh * 0.12);
      ctx.lineTo(x + half * 0.4, topY); ctx.lineTo(x + half, groundY); ctx.closePath(); ctx.fill();
      ctx.fillStyle = lit;
      ctx.beginPath();
      ctx.moveTo(x, topY - bh * 0.12); ctx.lineTo(x + half * 0.4, topY); ctx.lineTo(x + half, groundY); ctx.lineTo(x, groundY); ctx.closePath(); ctx.fill();
      break;
    }
    case 'ZIGGURAT': {
      // stepped pyramid: each tier narrower as it rises
      const steps = 3 + Math.floor(st.v1 * 3);
      const stepH = bh / steps;
      for (let s2 = 0; s2 < steps; s2++) {
        const ww = bw * (1 - s2 / (steps + 1));
        const yT = groundY - (s2 + 1) * stepH;
        ctx.fillStyle = layout.body; ctx.fillRect(x - ww / 2, yT, ww, stepH);
        ctx.fillStyle = lit; ctx.fillRect(x, yT, ww / 2, stepH);
        ctx.fillStyle = shade; ctx.fillRect(x - ww / 2, yT, ww / 2, stepH);
      }
      break;
    }
    case 'WINDTOWER': {
      box(topY, bh, bw * 0.6);
      // vented cap
      ctx.fillStyle = lit;
      ctx.fillRect(x - bw * 0.45, topY - 3, bw * 0.9, 3);
      break;
    }
    case 'KEEP': {
      // stone keep with crenellated top
      box(topY, bh, bw);
      ctx.fillStyle = layout.bodyLight;
      const merl = 4;
      for (let m = 0; m < merl; m++) {
        if (m % 2 === 0) ctx.fillRect(x - half + (m / merl) * bw, topY - 3, bw / merl, 3);
      }
      break;
    }
    case 'BATTLEMENT': {
      box(topY, bh, bw);
      ctx.fillStyle = layout.bodyLight;
      ctx.fillRect(x - half - 1, topY - 2, bw + 2, 2); // capped rampart
      break;
    }
    case 'FOUNDRY': {
      // tall foundry tower with a lava-glow crown
      box(topY, bh, bw);
      // glowing seams down the body
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = layout.accent; ctx.lineWidth = 1; ctx.globalAlpha = 0.7;
      ctx.beginPath(); ctx.moveTo(x - half * 0.4, topY + 4); ctx.lineTo(x - half * 0.4, groundY - 4); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x + half * 0.3, topY + 8); ctx.lineTo(x + half * 0.3, groundY - 4); ctx.stroke();
      // lava-glow crown
      const cg = ctx.createRadialGradient(x, topY, 0, x, topY, bw);
      cg.addColorStop(0, layout.accent); cg.addColorStop(1, 'rgba(255,90,30,0)');
      ctx.globalAlpha = 1; ctx.fillStyle = cg;
      ctx.fillRect(x - bw, topY - bw, bw * 2, bw * 1.4);
      ctx.restore();
      break;
    }
    case 'SMOKESTACK': {
      box(topY, bh, bw * 0.55);
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = layout.accent; ctx.globalAlpha = 0.6;
      ctx.fillRect(x - bw * 0.25, topY, bw * 0.5, 2); // ember mouth
      ctx.restore();
      break;
    }
    case 'ARCOLOGY': {
      // green tiered garden tower: a tapered stack with planted terrace lips
      const tiers = 3 + Math.floor(st.v1 * 2);
      const tierH = bh / tiers;
      for (let t2 = 0; t2 < tiers; t2++) {
        const ww = bw * (1 - t2 * 0.12);
        const yT = groundY - (t2 + 1) * tierH;
        ctx.fillStyle = layout.body; ctx.fillRect(x - ww / 2, yT, ww, tierH);
        ctx.fillStyle = lit; ctx.fillRect(x, yT, ww / 2, tierH);
        ctx.fillStyle = shade; ctx.fillRect(x - ww / 2, yT, ww / 2, tierH);
        // planted terrace lip (accent green)
        ctx.fillStyle = layout.accent; ctx.globalAlpha = 0.5;
        ctx.fillRect(x - ww / 2, yT + tierH - 2, ww, 2);
        ctx.globalAlpha = 1;
      }
      // crowning dome
      ctx.fillStyle = layout.bodyLight;
      ctx.beginPath(); ctx.arc(x, topY + 2, bw * 0.28, Math.PI, 0); ctx.fill();
      break;
    }
    case 'LIGHTHOUSE': {
      // STRIKING white tapered tower + subtle teal banding + a GLASS lamp room with a
      // warm-lit interior (the sweeping beam itself is drawn in the beacon pass).
      const lampH = bh * 0.14;
      const lampTop = topY;
      const towerTopY = topY + lampH;        // tower body stops below the lamp room
      const towerTopW = half * 0.5;
      // tapered body (trapezoid) — clean white, lit/shade faces.
      ctx.fillStyle = layout.body;
      ctx.beginPath();
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - towerTopW, towerTopY);
      ctx.lineTo(x + towerTopW, towerTopY); ctx.lineTo(x + half, groundY); ctx.closePath(); ctx.fill();
      ctx.save(); ctx.clip();
      ctx.fillStyle = lit; ctx.fillRect(x, towerTopY, half, bh);
      ctx.fillStyle = shade; ctx.fillRect(x - half, towerTopY, half, bh);
      // subtle TEAL accent bands wrapping the tower (3 thin rings).
      ctx.globalAlpha = 0.5; ctx.fillStyle = layout.accent;
      for (let b = 1; b <= 3; b++) ctx.fillRect(x - half, groundY - bh * (0.18 + b * 0.2), bw, 1.5);
      ctx.globalAlpha = 1;
      ctx.restore();
      // GALLERY rail beneath the lamp room (a thin teal trim ledge).
      ctx.fillStyle = layout.accent; ctx.globalAlpha = 0.8;
      ctx.fillRect(x - towerTopW * 1.25, towerTopY - 1.5, towerTopW * 2.5, 1.5);
      ctx.globalAlpha = 1;
      // GLASS lamp room: a small framed box with a warm-lit interior (night-aware).
      const lw = towerTopW * 1.8;
      ctx.fillStyle = layout.bodyDark;
      ctx.fillRect(x - lw / 2, lampTop, lw, lampH);
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      const lampGlow = t === 0 ? 0.7 : 0.6 + 0.3 * Math.sin(t * 1.2 + st.v1 * 6.28);
      const wg2 = layout.winGlow;
      ctx.fillStyle = `rgba(${wg2.r}, ${wg2.g}, ${wg2.b}, ${(0.8 * lampGlow * (0.5 + winNight * 0.5)).toFixed(3)})`;
      ctx.fillRect(x - lw / 2 + 1, lampTop + 1, lw - 2, lampH - 2);
      ctx.restore();
      // domed cap on the lamp room.
      ctx.fillStyle = layout.bodyLight;
      ctx.beginPath(); ctx.ellipse(x, lampTop, lw * 0.5, lampH * 0.5, 0, Math.PI, 0); ctx.fill();
      break;
    }
    // --- VOLCANIC forge-city kinds (pass 2) ---
    case 'SMELTER_CRUCIBLE': {
      // tapered tower (wider base → narrower top), open crucible U-notch on top
      // with a glowing molten core, + horizontal slag banding lines.
      const tw = bw * 0.62;                       // top width
      const notchTop = topY + bh * 0.20;          // crucible occupies top ~20%
      // tapered body (trapezoid), lit/shade faces
      ctx.fillStyle = layout.body;
      ctx.beginPath();
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - tw / 2, topY);
      ctx.lineTo(x + tw / 2, topY); ctx.lineTo(x + half, groundY); ctx.closePath(); ctx.fill();
      ctx.save(); ctx.clip();
      ctx.fillStyle = lit; ctx.fillRect(x, topY, half, bh);
      ctx.fillStyle = shade; ctx.fillRect(x - half, topY, half, bh);
      // slag banding lines across the body (additive heat-metal)
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = layout.accent; ctx.globalAlpha = 0.3;
      ctx.lineWidth = 1;
      for (let b = 1; b <= 4; b++) {
        const by = topY + bh * (0.25 + b * 0.16);
        ctx.beginPath(); ctx.moveTo(x - half, by); ctx.lineTo(x + half, by); ctx.stroke();
      }
      ctx.restore();
      // open crucible notch (carve a U) + molten core glow fanning up
      ctx.fillStyle = layout.bodyDark;
      ctx.fillRect(x - tw * 0.32, topY, tw * 0.64, notchTop - topY);
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      const pulse = t === 0 ? 0.75 : 0.6 + 0.4 * Math.sin(t * 1.6 + st.v1 * 6.28);
      const mg = ctx.createRadialGradient(x, topY + 1, 0, x, topY + 1, tw);
      mg.addColorStop(0, `rgba(255, 210, 120, ${(0.85 * pulse).toFixed(3)})`);
      mg.addColorStop(0.4, `rgba(255, 100, 30, ${(0.5 * pulse).toFixed(3)})`);
      mg.addColorStop(1, 'rgba(255, 60, 0, 0)');
      ctx.fillStyle = mg;
      ctx.fillRect(x - tw, topY - tw, tw * 2, tw * 1.6);
      ctx.restore();
      break;
    }
    case 'BLAST_FURNACE_DOME': {
      // squat hemispherical furnace (flat base, dome top) + top opening glow + seams
      const r = Math.max(half, bh * 0.9);
      ctx.fillStyle = layout.body;
      ctx.beginPath(); ctx.arc(x, groundY, r, Math.PI, 0); ctx.fill();
      ctx.fillStyle = lit;
      ctx.beginPath(); ctx.arc(x, groundY, r, Math.PI, Math.PI * 1.5); ctx.fill();
      // hot seam lines up the sides (additive)
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = layout.accent; ctx.globalAlpha = 0.4; ctx.lineWidth = 1;
      for (let a = -1; a <= 1; a += 1) {
        const ax = x + a * r * 0.5;
        ctx.beginPath(); ctx.moveTo(ax, groundY); ctx.lineTo(x + a * r * 0.2, groundY - r * 0.9); ctx.stroke();
      }
      // top opening: a directional orange bloom blooming UPWARD from the crown
      const pulse = t === 0 ? 0.75 : 0.6 + 0.4 * Math.sin(t * 1.4 + st.v2 * 6.28);
      const og = ctx.createRadialGradient(x, groundY - r, 0, x, groundY - r, r * 0.9);
      og.addColorStop(0, `rgba(255, 180, 90, ${(0.8 * pulse).toFixed(3)})`);
      og.addColorStop(1, 'rgba(255, 80, 20, 0)');
      ctx.globalAlpha = 1; ctx.fillStyle = og;
      ctx.fillRect(x - r, groundY - r * 1.9, r * 2, r * 1.2);
      ctx.restore();
      break;
    }
    case 'HEAT_VENT_TOWER': {
      // slender tower + a FLARED FUNNEL CAP (trapezoid wider at top) lit hot, plus a
      // soft additive glow column rising above the cap.
      const tbw = bw * 0.56;
      box(topY + bh * 0.10, bh * 0.90, tbw);
      // flared funnel cap (wider at the top)
      const capH = bh * 0.12;
      ctx.fillStyle = lit;
      ctx.beginPath();
      ctx.moveTo(x - tbw * 0.5, topY + capH); ctx.lineTo(x - tbw * 0.9, topY);
      ctx.lineTo(x + tbw * 0.9, topY); ctx.lineTo(x + tbw * 0.5, topY + capH); ctx.closePath(); ctx.fill();
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      // hot funnel mouth
      ctx.fillStyle = layout.accent; ctx.globalAlpha = 0.7;
      ctx.fillRect(x - tbw * 0.9, topY - 1, tbw * 1.8, 2);
      // glow column rising ~20px above the cap
      const pulse = t === 0 ? 0.7 : 0.55 + 0.45 * Math.sin(t * 2 + st.v1 * 6.28);
      const colH = 20;
      const gc = ctx.createLinearGradient(0, topY - colH, 0, topY);
      gc.addColorStop(0, 'rgba(255, 90, 30, 0)');
      gc.addColorStop(1, `rgba(255, 130, 50, ${(0.4 * pulse).toFixed(3)})`);
      ctx.globalAlpha = 1; ctx.fillStyle = gc;
      ctx.fillRect(x - tbw * 0.5, topY - colH, tbw, colH);
      ctx.restore();
      break;
    }
    case 'OBSIDIAN_BUNKER': {
      // low wide angular bunker: parallelogram with inward-sloped walls + roof lip +
      // a full-width glowing slit-window strip. Dark blue-black obsidian body.
      const inset = bw * 0.16;
      ctx.fillStyle = 'rgb(20, 18, 28)';          // obsidian (cool dark) to contrast warm
      ctx.beginPath();
      ctx.moveTo(x - half, groundY); ctx.lineTo(x - half + inset, topY);
      ctx.lineTo(x + half - inset, topY); ctx.lineTo(x + half, groundY); ctx.closePath(); ctx.fill();
      // lit face (right half) for the key-light
      ctx.save(); ctx.clip();
      ctx.fillStyle = faceSign >= 0 ? 'rgba(70, 66, 86, 0.6)' : 'rgba(8, 6, 14, 0.5)';
      ctx.fillRect(x, topY, half, bh);
      ctx.restore();
      // thick roof overhang lip
      ctx.fillStyle = 'rgb(36, 32, 46)';
      ctx.fillRect(x - half + inset * 0.5, topY - 3, bw - inset, 4);
      // full-width glowing slit-window strip (additive)
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      const sy = topY + bh * 0.4;
      const sg = ctx.createLinearGradient(x - half, sy, x + half, sy);
      sg.addColorStop(0, 'rgba(255, 90, 30, 0)');
      sg.addColorStop(0.5, `rgba(255, 140, 60, ${(0.7 * winNight).toFixed(3)})`);
      sg.addColorStop(1, 'rgba(255, 90, 30, 0)');
      ctx.fillStyle = sg;
      ctx.fillRect(x - half + inset * 0.6, sy, bw - inset * 1.2, 2.4);
      ctx.restore();
      break;
    }
    case 'MAGMA_PIPELINE': {
      // two short pillars + a horizontal pipe at mid-height glowing orange underneath
      const pw = bw * 0.22;
      const pipeY = topY + bh * 0.45;
      // two pillars (drawn manually — the box helper would centre a single block)
      ctx.fillStyle = layout.body; ctx.fillRect(x - half, pipeY, pw, groundY - pipeY);
      ctx.fillStyle = layout.body; ctx.fillRect(x + half - pw, pipeY, pw, groundY - pipeY);
      ctx.fillStyle = lit; ctx.fillRect(x - half + pw * 0.5, pipeY, pw * 0.5, groundY - pipeY);
      ctx.fillStyle = lit; ctx.fillRect(x + half - pw * 0.5, pipeY, pw * 0.5, groundY - pipeY);
      // the spanning pipe
      ctx.fillStyle = layout.bodyLight;
      ctx.fillRect(x - half, pipeY - 3, bw, 5);
      // glowing underside (additive)
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      const pulse = t === 0 ? 0.7 : 0.55 + 0.45 * Math.sin(t * 1.8 + st.v1 * 6.28);
      ctx.fillStyle = `rgba(255, 110, 40, ${(0.6 * pulse * winNight).toFixed(3)})`;
      ctx.fillRect(x - half, pipeY + 2, bw, 1.6);
      ctx.restore();
      break;
    }
    case 'CATWALK_GANTRY': {
      // skeletal column + horizontal gantry arm + a small lit observation pod at tip.
      const dir = st.v1 > 0.5 ? 1 : -1;
      const cw = bw * 0.36;
      ctx.fillStyle = layout.body; ctx.fillRect(x - cw / 2, topY, cw, bh);
      ctx.fillStyle = lit; ctx.fillRect(x, topY, cw / 2, bh);
      // gantry arm
      const armY = topY + bh * 0.2;
      const armLen = bw * 0.9 * dir;
      ctx.strokeStyle = layout.bodyLight; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x, armY); ctx.lineTo(x + armLen, armY); ctx.stroke();
      // diagonal brace
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, armY + bh * 0.18); ctx.lineTo(x + armLen, armY); ctx.stroke();
      // lit observation pod at the arm tip
      const podX = x + armLen;
      ctx.fillStyle = layout.bodyLight;
      ctx.beginPath(); ctx.arc(podX, armY, Math.max(2.5, cw * 0.6), 0, Math.PI * 2); ctx.fill();
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = `rgba(${layout.winGlow.r}, ${layout.winGlow.g}, ${layout.winGlow.b}, ${(0.7 * winNight).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(podX, armY, Math.max(1.5, cw * 0.35), 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      break;
    }
    // --- OCEANIC coastal-future kinds (pass 3) ---
    case 'SEADOME': {
      // translucent SEA-BLUE glass observation dome: a low base + a tinted glass dome
      // with a warm-lit interior and a bright specular sheen sweeping the upper-left.
      const r = half * 1.15;
      const cy = groundY - r * 0.4;
      box(cy, r * 0.4, bw);                       // low base ring
      // warm interior glow first (shows THROUGH the translucent glass at night).
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      const wg3 = layout.winGlow;
      ctx.fillStyle = `rgba(${wg3.r}, ${wg3.g}, ${wg3.b}, ${(0.3 * (0.4 + winNight * 0.6)).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(x, cy, r * 0.8, Math.PI, 0); ctx.fill();
      ctx.restore();
      // translucent blue glass dome.
      ctx.save();
      ctx.fillStyle = layout.accent; ctx.globalAlpha = 0.32;
      ctx.beginPath(); ctx.arc(x, cy, r, Math.PI, 0); ctx.fill();
      ctx.globalAlpha = 1;
      // glass framing meridians (thin teal seams).
      ctx.strokeStyle = layout.accent; ctx.globalAlpha = 0.5; ctx.lineWidth = 1;
      for (let f = 1; f < 4; f++) { const a = Math.PI + (f / 4) * Math.PI; ctx.beginPath(); ctx.moveTo(x, cy); ctx.lineTo(x + Math.cos(a) * r, cy + Math.sin(a) * r); ctx.stroke(); }
      ctx.beginPath(); ctx.ellipse(x, cy, r * 0.6, r * 0.3, 0, Math.PI, 0); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.restore();
      // bright specular GLASS sheen (upper-left arc).
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)'; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.arc(x, cy, r * 0.78, Math.PI * 1.12, Math.PI * 1.42); ctx.stroke();
      break;
    }
    case 'DESAL_TOWER': {
      // coastal desalination tower: a cylindrical white body, a side intake PIPE pair,
      // a top tank, and a teal accent band. Reads as clean maritime infrastructure.
      const cw = bw * 0.66;                       // cylinder narrower than footprint
      const tankH = bh * 0.16;
      const bodyTop = topY + tankH;
      // cylinder body with lit/shade faces + rounded top via an ellipse cap.
      ctx.fillStyle = layout.body; ctx.fillRect(x - cw / 2, bodyTop, cw, groundY - bodyTop);
      ctx.fillStyle = lit; ctx.fillRect(x, bodyTop, cw / 2, groundY - bodyTop);
      ctx.fillStyle = shade; ctx.fillRect(x - cw / 2, bodyTop, cw * 0.18, groundY - bodyTop);
      // external pipes running up one side.
      const dir = st.v1 > 0.5 ? 1 : -1;
      ctx.strokeStyle = layout.bodyDark; ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x + dir * cw * 0.42, groundY); ctx.lineTo(x + dir * cw * 0.42, bodyTop + tankH * 0.5);
      ctx.moveTo(x + dir * cw * 0.6, groundY); ctx.lineTo(x + dir * cw * 0.6, bodyTop + tankH);
      ctx.stroke();
      // pipe cross-braces
      ctx.lineWidth = 1;
      for (let p = 1; p <= 3; p++) { const py = groundY - (groundY - bodyTop) * (p / 4); ctx.beginPath(); ctx.moveTo(x + dir * cw * 0.42, py); ctx.lineTo(x + dir * cw * 0.6, py); ctx.stroke(); }
      // top TANK (rounded).
      ctx.fillStyle = layout.bodyLight;
      ctx.beginPath(); ctx.ellipse(x, bodyTop, cw * 0.62, tankH, 0, Math.PI, 0); ctx.fill();
      ctx.fillRect(x - cw * 0.62, bodyTop, cw * 1.24, 2);
      // teal accent band around the body.
      ctx.fillStyle = layout.accent; ctx.globalAlpha = 0.7;
      ctx.fillRect(x - cw / 2, groundY - (groundY - bodyTop) * 0.5, cw, 2);
      ctx.globalAlpha = 1;
      break;
    }
    case 'STILT_PLATFORM': {
      // a raised railed DECK on stilts built out over the water. Deck cantilevers past
      // the legs; a lit deck edge + railing posts read as an over-water platform.
      const deckH = Math.max(4, bh * 0.34);
      const deckTop = groundY - bh;
      const deckW = bw * 1.15;                    // cantilevers past the footprint
      const legTop = deckTop + deckH;
      // stilt legs down to / below the waterline.
      ctx.strokeStyle = layout.bodyDark; ctx.lineWidth = 1.6;
      for (const lx of [-deckW * 0.4, -deckW * 0.12, deckW * 0.18, deckW * 0.42]) {
        ctx.beginPath(); ctx.moveTo(x + lx, legTop); ctx.lineTo(x + lx, groundY + bh * 0.16); ctx.stroke();
      }
      // the deck slab (lit top edge).
      ctx.fillStyle = layout.body; ctx.fillRect(x - deckW / 2, deckTop, deckW, deckH);
      ctx.fillStyle = lit; ctx.fillRect(x - deckW / 2, deckTop, deckW, Math.max(1.5, deckH * 0.4));
      ctx.fillStyle = shade; ctx.fillRect(x - deckW / 2, deckTop + deckH - 1.5, deckW, 1.5);
      // teal railing along the seaward edge (posts + top rail).
      ctx.strokeStyle = layout.accent; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x - deckW / 2, deckTop - 3); ctx.lineTo(x + deckW / 2, deckTop - 3); ctx.stroke();
      for (let p = 0; p <= 5; p++) { const px = x - deckW / 2 + (deckW * p) / 5; ctx.beginPath(); ctx.moveTo(px, deckTop - 3); ctx.lineTo(px, deckTop); ctx.stroke(); }
      // a small lit deck lamp (night-aware).
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      const wg4 = layout.winGlow;
      ctx.fillStyle = `rgba(${wg4.r}, ${wg4.g}, ${wg4.b}, ${(0.6 * winNight).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(x + deckW * 0.35, deckTop - 3, 1.8, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      break;
    }
    case 'SAIL_HALL': {
      // a white tensile/SAIL peaked-roof hall (marina convention look): a low wall +
      // two swept sail peaks with a teal ridge seam; a bright sun-lit sail face.
      const wallH = bh * 0.32;
      const wallTop = groundY - wallH;
      box(wallTop, wallH, bw);                    // low base wall
      const peakY = topY;                          // sail apex
      const midY = topY + bh * 0.22;
      // two swept sail panels (left + right), meeting at a central mast.
      ctx.fillStyle = layout.body;
      ctx.beginPath();
      ctx.moveTo(x - half, wallTop);
      ctx.quadraticCurveTo(x - half * 0.5, midY, x, peakY);     // left sail sweep
      ctx.quadraticCurveTo(x + half * 0.5, midY, x + half, wallTop);
      ctx.closePath(); ctx.fill();
      // sun-lit right sail face.
      ctx.fillStyle = lit;
      ctx.beginPath();
      ctx.moveTo(x, peakY);
      ctx.quadraticCurveTo(x + half * 0.5, midY, x + half, wallTop);
      ctx.lineTo(x, wallTop); ctx.closePath(); ctx.fill();
      // teal ridge seam from apex down to the wall + a thin mast.
      ctx.strokeStyle = layout.accent; ctx.lineWidth = 1.2; ctx.globalAlpha = 0.8;
      ctx.beginPath(); ctx.moveTo(x, peakY); ctx.lineTo(x, wallTop); ctx.stroke();
      ctx.globalAlpha = 0.5;
      ctx.beginPath(); ctx.moveTo(x - half, wallTop); ctx.quadraticCurveTo(x - half * 0.5, midY, x, peakY); ctx.stroke();
      ctx.globalAlpha = 1;
      break;
    }
    default:
      box(topY, bh, bw);
  }

  // 7) LAVA RIM-LIGHT WASH — all VOLCANIC structures get an additive linear glow up
  //    the lower ~18% of the body (lava-pool bounce-light); brighter at night (×1.5).
  if (volc && st.kind !== 'MAST') {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const rh = bh * 0.18;
    const rg = ctx.createLinearGradient(0, groundY, 0, groundY - rh);
    const ra = Math.min(0.6, 0.3 * (1 + (winNight - 0.5)));   // ~×1.5 at night
    rg.addColorStop(0, `rgba(255, 90, 20, ${ra.toFixed(3)})`);
    rg.addColorStop(1, 'rgba(255, 90, 20, 0)');
    ctx.fillStyle = rg;
    ctx.fillRect(x - half, groundY - rh, bw, rh);
    ctx.restore();

    // 7b) CONTRAST OUTLINE — a thin warm rim around the silhouette so the dark forge
    //     body separates from the dark terrain at DAY/DUSK (citadel visibility).
    ctx.save();
    ctx.beginPath();
    clipStructPath(ctx, st, groundY);
    ctx.strokeStyle = 'rgba(255, 150, 90, 0.55)';
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.restore();
  }
}

/** Per-frame citadel draw — re-anchors the cached biome+level layout to the live
 *  ridge, paints each structure in its biome vocabulary with a sun key-light +
 *  ground shadow, then window glow (twinkle at L4+) and the beacon/lamp pulse. */
function drawCitadelSkyline(
  ctx: CanvasRenderingContext2D,
  h: number,
  t: number,
  layout: CitadelLayout,
  ridgeYAt: (x: number) => number,
  keyDir: number,
  winNight: number
): void {
  ctx.save();
  // draw back-to-front: sort by x distance from centre so nearer-centre (usually
  // the tall signature) overlaps cleanly — cheap insertion via index order is fine
  // since the layout was emitted signature-first; paint fillers first, signature last.
  const order = layout.structures
    .map((_, i) => i)
    .sort((a, b) => (layout.structures[a].signature ? 1 : 0) - (layout.structures[b].signature ? 1 : 0));
  for (const s of order) {
    const st = layout.structures[s];
    // FOUNDATION FLOOR: sample the terrain across the FULL footprint and floor the
    // building at the HIGHEST point (min Y) so no edge of its base hangs over a
    // drop. drawCitadelStruct then pours a foundation skirt down to the real
    // terrain across the width, so the base meets the ground along its whole span.
    const floorY = footprintFloorY(st, ridgeYAt);
    drawCitadelStruct(ctx, st, floorY, layout, keyDir, ridgeYAt, t, winNight);
  }

  // warm window lights — biome-tinted glow; twinkle subtly at higher levels. Each
  // structure's window grid is CLIPPED to its actual silhouette so lights on domes /
  // ziggurats / tapered towers never spill outside the drawn body as a rectangle.
  const wg = layout.winGlow;
  let winIndex = 0;
  for (let s = 0; s < layout.structures.length; s++) {
    const st = layout.structures[s];
    if (st.windows.length === 0) continue;
    // use the SAME foundation floor the body was drawn from so windows align.
    const groundY = footprintFloorY(st, ridgeYAt);
    const topY = groundY - st.bh;
    ctx.save();
    ctx.beginPath();
    clipStructPath(ctx, st, groundY);
    ctx.clip();
    ctx.globalCompositeOperation = 'lighter';
    for (let k = 0; k < st.windows.length; k++) {
      const win = st.windows[k];
      const tw = layout.twinkleWindows
        ? (t === 0 ? 0.85 : 0.6 + 0.4 * Math.sin(t * 2 + winIndex * 1.3))
        : 0.85;
      winIndex++;
      const warm = win.warm;
      const r = Math.min(255, wg.r);
      const g = Math.min(255, Math.round(wg.g * (0.85 + warm * 0.15)));
      const b = Math.min(255, Math.round(wg.b * (0.85 + warm * 0.15)));
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${(0.55 * tw * winNight).toFixed(3)})`;
      ctx.fillRect(st.x + win.dx, topY + win.dy, 1.8, 1.8);
    }
    ctx.restore();
  }
  ctx.globalCompositeOperation = 'source-over';

  // Beacon — L5 pulsing red aircraft-warning light, OR the oceanic lighthouse
  // lamp (a sweeping green-white pulse from the signature lighthouse top).
  if (layout.beacon) {
    const sig = layout.structures.find((s) => s.signature) || layout.structures[0];
    const bx = sig ? sig.x : layout.cityX;
    // sit the beacon atop the signature's FLOORED top (same foundation floor as the
    // body) so it caps the tower, not a center-only ground sample.
    const sigFloor = sig ? footprintFloorY(sig, ridgeYAt) : ridgeYAt(bx);
    const by = sigFloor - (sig ? sig.bh : h * layout.maxH) - 4;
    const pulse = t === 0 ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.4);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    if (layout.beaconKind === 'LAVA_PLUME') {
      // CALDERA BEACON: a sustained vertical LAVA-PLUME column rising ~48px from the
      // signature's crown — white-orange base → red → transparent; width oscillates.
      const plumeH = 48;
      const wob = t === 0 ? 0 : Math.sin(t * 1.8) * 2;
      const baseW = 5 + wob;
      const pg = ctx.createLinearGradient(0, by, 0, by - plumeH);
      pg.addColorStop(0, 'rgba(255, 220, 150, 0.9)');
      pg.addColorStop(0.45, 'rgba(255, 90, 30, 0.5)');
      pg.addColorStop(1, 'rgba(255, 50, 10, 0)');
      ctx.fillStyle = pg;
      // a tapering column (wider at base) via a trapezoid
      ctx.beginPath();
      ctx.moveTo(bx - baseW, by);
      ctx.lineTo(bx - baseW * 0.3, by - plumeH);
      ctx.lineTo(bx + baseW * 0.3, by - plumeH);
      ctx.lineTo(bx + baseW, by);
      ctx.closePath(); ctx.fill();
      // bright molten core at the base
      const cg = ctx.createRadialGradient(bx, by, 0, bx, by, baseW * 2);
      cg.addColorStop(0, 'rgba(255, 230, 170, 0.9)');
      cg.addColorStop(1, 'rgba(255, 90, 30, 0)');
      ctx.fillStyle = cg;
      ctx.fillRect(bx - baseW * 2, by - baseW * 2, baseW * 4, baseW * 4);
    } else if (layout.beaconKind === 'LAMP') {
      // lighthouse lamp: a bright white-teal core + a slow rotating BEAM sweeping out
      // over the sea. Clean maritime light (matches the coastal palette). Static at t=0.
      const lr = 17;
      const lg = ctx.createRadialGradient(bx, by, 0, bx, by, lr);
      lg.addColorStop(0, `rgba(220, 255, 252, ${(0.6 + pulse * 0.4).toFixed(3)})`);
      lg.addColorStop(0.5, `rgba(120, 230, 225, ${(0.4 + pulse * 0.3).toFixed(3)})`);
      lg.addColorStop(1, 'rgba(80, 210, 205, 0)');
      ctx.fillStyle = lg;
      ctx.fillRect(bx - lr, by - lr, lr * 2, lr * 2);
      // sweeping beam — a thin rotating wedge with a soft falloff gradient; the sweep
      // is biased to arc out over the OPEN sea side away from the land mass.
      const ang = t === 0 ? -0.55 : (t * 0.7) % (Math.PI * 2);
      const beamLen = 78;
      for (let pass = 0; pass < 2; pass++) {            // wide soft beam + tight bright core
        const spread = pass === 0 ? 0.18 : 0.07;
        const ex1 = bx + Math.cos(ang - spread) * beamLen, ey1 = by + Math.sin(ang - spread) * beamLen * 0.5;
        const ex2 = bx + Math.cos(ang + spread) * beamLen, ey2 = by + Math.sin(ang + spread) * beamLen * 0.5;
        const bg = ctx.createLinearGradient(bx, by, (ex1 + ex2) / 2, (ey1 + ey2) / 2);
        const a = (pass === 0 ? 0.18 : 0.4) * (0.6 + pulse * 0.4);
        bg.addColorStop(0, `rgba(225, 255, 252, ${a.toFixed(3)})`);
        bg.addColorStop(1, 'rgba(150, 235, 230, 0)');
        ctx.fillStyle = bg;
        ctx.beginPath();
        ctx.moveTo(bx, by); ctx.lineTo(ex1, ey1); ctx.lineTo(ex2, ey2); ctx.closePath(); ctx.fill();
      }
    } else {
      const bg = ctx.createRadialGradient(bx, by, 0, bx, by, 12);
      bg.addColorStop(0, `rgba(255, 70, 60, ${(0.5 + pulse * 0.45).toFixed(3)})`);
      bg.addColorStop(1, 'rgba(255, 70, 60, 0)');
      ctx.fillStyle = bg;
      ctx.fillRect(bx - 12, by - 12, 24, 24);
      ctx.beginPath();
      ctx.arc(bx, by, 2, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 120, 110, ${(0.7 + pulse * 0.3).toFixed(3)})`;
      ctx.fill();
    }
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
