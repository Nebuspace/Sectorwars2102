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
  real: boolean;
  planet_id?: string;
  name?: string;
  habitability?: number;
  owned?: boolean;
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

type Treatment = 'GAS_GIANT' | 'BARREN' | 'ICE' | 'VOLCANIC' | 'DESERT' | 'TERRAN' | 'OCEANIC';

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
  seed: number
): void {
  const rng = splitmix32(seed);
  const hue = body.palette.hue;
  const sat = body.palette.sat;
  const treatment = treatmentFor(body.kind);

  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.clip();

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
  drawPlanetSurface(ctx, body, cx, cy, r, lightX, lightY, seed);
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
          `${body.kind.replace(/_/g, ' ').toUpperCase()}${hab}${body.owned ? ' — CLAIMED' : ''}`
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
        const ringTilt = -0.32 + ((seed % 100) / 100 - 0.5) * 0.3;
        if (body.rings) drawRingHalf(ctx, x, y, r, body.palette.hue, ringTilt, 'back');
        drawPlanetSurface(ctx, body, x, y, r, starX, starY, seed);
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
  const treatment = treatmentFor(planetType || '');
  const effective = treatment === 'BARREN' && !KNOWN_BARREN.has(kind)
    ? 'GAS_GIANT' // violet-dusk default branch
    : treatment;
  switch (effective) {
    case 'VOLCANIC':
      return {
        skyTop: '#120305', skyMid: '#3a0d08', horizon: '#8a2e0a',
        glow: 'rgba(255, 110, 30, 0.5)', haze: '255, 90, 20',
        ridges: ['#2a0c08', '#1a0705', '#0c0303']
      };
    case 'ICE':
      return {
        skyTop: '#0c1622', skyMid: '#27435c', horizon: '#9cc4dd',
        glow: 'rgba(210, 235, 255, 0.45)', haze: '190, 220, 240',
        ridges: ['#5d7c93', '#3b566c', '#1d2f40']
      };
    case 'TERRAN':
      return {
        skyTop: '#04121f', skyMid: '#0d3a4a', horizon: '#2f8c74',
        glow: 'rgba(150, 230, 200, 0.4)', haze: '120, 210, 180',
        ridges: ['#14463c', '#0d2f29', '#061a16']
      };
    case 'OCEANIC':
      return {
        skyTop: '#03101f', skyMid: '#0a3550', horizon: '#2a7f9e',
        glow: 'rgba(120, 210, 235, 0.4)', haze: '110, 190, 220',
        ridges: ['#0f3f55', '#0a2b3c', '#051824']
      };
    case 'DESERT':
      return {
        skyTop: '#190b04', skyMid: '#4a2410', horizon: '#c07a2e',
        glow: 'rgba(255, 190, 90, 0.45)', haze: '230, 160, 70',
        ridges: ['#5c3014', '#3c1f0c', '#201006']
      };
    case 'BARREN':
      return {
        skyTop: '#0a0a12', skyMid: '#23232f', horizon: '#5a5a6e',
        glow: 'rgba(190, 190, 210, 0.3)', haze: '160, 160, 180',
        ridges: ['#3a3a4a', '#26262f', '#131318']
      };
    case 'GAS_GIANT':
    default:
      // Violet dusk — matches the legacy landed-band gradient language
      return {
        skyTop: '#120822', skyMid: '#2d1a3d', horizon: '#6a4a8a',
        glow: 'rgba(190, 140, 255, 0.4)', haze: '170, 120, 240',
        ridges: ['#3a2a4f', '#241a33', '#120c1c']
      };
  }
}

function drawLandedScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sectorId: number,
  t: number,
  pal: LandedPalette
): void {
  const horizonY = h * 0.58;

  // 1) Sky gradient — top of atmosphere down to the horizon line
  const sky = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
  sky.addColorStop(0, pal.skyTop);
  sky.addColorStop(0.6, pal.skyMid);
  sky.addColorStop(1, pal.horizon);
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, w, h);

  // 2) Low sun / atmospheric glow near the horizon (seeded x per sector)
  const anchorRng = splitmix32(sectorId * 911 + 3);
  const gx = w * (0.25 + anchorRng() * 0.5);
  const glow = ctx.createRadialGradient(gx, horizonY, 0, gx, horizonY, Math.max(w, h) * 0.45);
  glow.addColorStop(0, pal.glow);
  glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);

  // 3) Parallax ridge layers (3, back → front) — deterministic jagged
  //    silhouettes sampled from a wrapping noise strip; each layer drifts
  //    at its own speed for depth.
  const layers = [
    { base: 0.6, amp: 0.1, speed: 1.2, seed: 5, color: pal.ridges[0] },
    { base: 0.7, amp: 0.13, speed: 2.6, seed: 11, color: pal.ridges[1] },
    { base: 0.84, amp: 0.16, speed: 4.6, seed: 23, color: pal.ridges[2] }
  ];
  for (const layer of layers) {
    const rng = splitmix32(sectorId * 131 + layer.seed);
    const period = Math.max(w * 2, 1200);
    const n = 48;
    const pts: number[] = [];
    for (let i = 0; i < n; i++) pts.push(rng());
    const off = t * layer.speed;
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let x = 0; x <= w; x += 8) {
      const u = (((x + off) % period) + period) % period;
      const fi = (u / period) * n;
      const i0 = Math.floor(fi) % n;
      const i1 = (i0 + 1) % n;
      const frac = fi - Math.floor(fi);
      const s = frac * frac * (3 - 2 * frac); // smoothstep — soft crests
      const v = pts[i0] * (1 - s) + pts[i1] * s;
      ctx.lineTo(x, h * layer.base - v * h * layer.amp);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = layer.color;
    ctx.fill();
  }

  // 4) Atmospheric haze — wide translucent bands drifting slowly
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const hazeRng = splitmix32(sectorId * 53 + 7);
  for (let i = 0; i < 3; i++) {
    const hy = h * (0.5 + i * 0.13) + Math.sin(t * 0.05 + i * 2.1) * 4;
    const hx = ((hazeRng() * w + t * (3 + i * 2)) % (w * 1.4)) - w * 0.2;
    const hw = w * (0.5 + hazeRng() * 0.3);
    const grad = ctx.createRadialGradient(hx, hy, 0, hx, hy, hw);
    grad.addColorStop(0, `rgba(${pal.haze}, 0.07)`);
    grad.addColorStop(1, `rgba(${pal.haze}, 0)`);
    ctx.fillStyle = grad;
    // Squash the blob into a horizontal haze band
    ctx.save();
    ctx.translate(hx, hy);
    ctx.scale(1, 0.22);
    ctx.translate(-hx, -hy);
    ctx.fillRect(hx - hw, hy - hw, hw * 2, hw * 2);
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
  // Scene mode + per-scene parameters, ref-mirrored for the draw loop
  const sceneRef = useRef({ scene, isSpaceDock, palette: landedPalette(planetType) });
  sceneRef.current = { scene, isSpaceDock, palette: landedPalette(planetType) };

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
      drawLandedScene(ctx, w, h, sectorId, t, sceneRef.current.palette);
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
    if (scene !== 'flight') {
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
    if (fetchFailed) return;

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
      if (!boosted && now - lastDrawRef.current < BASE_FRAME_MS) return;
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
  }, [fetchFailed, system, sectorId, reducedMotion, scene, planetType, isSpaceDock, orbit]);

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
  if (fetchFailed) {
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
                position: 'absolute', top: 8, right: 8, zIndex: 6, ...glass,
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
            </div>
          </>
        );
      })()}
    </div>
  );
};

export default SolarSystemViewscreen;
