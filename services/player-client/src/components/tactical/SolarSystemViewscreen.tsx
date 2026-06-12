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
}

/** Per-kind payload backing the click popup card */
type HitMeta =
  | { kind: 'star'; label: string; starClass: string; color: string }
  | { kind: 'planet'; planetId: string; planetKind: string; habitability?: number; owned?: boolean }
  | { kind: 'station'; stationId: string; stationType: string }
  | { kind: 'procedural'; designation: string; typeName: string; sizeDesc: string };

interface HitTarget {
  /** screen-space hit data in CSS pixels (the draw loop paints through a
      setTransform(dpr, …) so every recorded coordinate is CSS-pixel space) */
  x: number;
  y: number;
  r: number;
  kind: 'star' | 'planet' | 'station' | 'procedural';
  id?: string;
  name: string;
  lines: string[];
  meta: HitMeta;
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
  radiationLevel: number
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
  const starX = w * 0.3 + (anchorRng() - 0.5) * 2 * (w * 0.03);
  const starY = h * 0.52 + (anchorRng() - 0.5) * 2 * (h * 0.04);
  const margin = 14;
  // Cap the orbital extent so the outermost ellipse never drifts off the left
  // edge (which would put hit targets out of reach): (starX - margin) bounds
  // the leftward reach in addition to the right/vertical bounds.
  const rxMax = Math.min(w * 0.64, (h * 0.5 - margin) / SQUASH, starX - margin);
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

  // 6) Asteroid belt — speckled annulus, two passes for depth
  const drawBelt = (pass: 'back' | 'front') => {
    if (!system.belt) return;
    const rng = splitmix32(sectorId * 41 + 1337);
    const count = 110;
    for (let i = 0; i < count; i++) {
      const frac = system.belt.inner_au + rng() * Math.max(0.01, system.belt.outer_au - system.belt.inner_au);
      const a0 = rng() * Math.PI * 2;
      const speed = (0.018 + rng() * 0.014) / Math.max(0.1, frac);
      const size = 0.5 + rng() * 1.1;
      const alpha = 0.2 + rng() * 0.4;
      const ang = a0 + t * speed;
      const ax = starX + Math.cos(ang) * frac * rxMax;
      const ay = starY + Math.sin(ang) * frac * rxMax * SQUASH;
      const isBack = ay < starY;
      if ((pass === 'back') !== isBack) continue;
      ctx.globalAlpha = alpha;
      ctx.fillStyle = '#aaaabe';
      ctx.fillRect(ax, ay, size, size);
    }
    ctx.globalAlpha = 1;
  };
  drawBelt('back');

  // 3/5) Star + bodies + stations, depth-sorted by screen y
  const drawables: Array<{ y: number; draw: () => void }> = [];

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
    // Angular speed ~ 1/orbit_au — full orbit takes minutes
    const omega = (Math.PI * 2) / (180 + body.orbit_au * 420);
    const ang = (body.phase_deg * Math.PI) / 180 + t * omega;
    const x = starX + Math.cos(ang) * rx;
    const y = starY + Math.sin(ang) * ry;
    let r = (3 + body.size_class * 2.1) * bodyScale;
    if (body.real) r *= 1.2;
    const seed = (sectorId * 101 + body.slot * 7919 + Math.round(body.palette.hue)) >>> 0;

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

        // Moons — tiny dots orbiting close
        const moonRng = splitmix32(seed + 9);
        for (let m = 0; m < body.moons; m++) {
          const mo = moonRng() * Math.PI * 2;
          const ms = 0.4 + moonRng() * 0.5;
          const mr = r + 3 + m * 3.2;
          const ma = mo + t * ms;
          ctx.beginPath();
          ctx.arc(x + Math.cos(ma) * mr, y + Math.sin(ma) * mr * 0.6, 1.1, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(200, 200, 215, 0.8)';
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
    const omega = (Math.PI * 2) / (160 + st.orbit_au * 380);
    const ang = (st.phase_deg * Math.PI) / 180 + t * omega;
    const x = starX + Math.cos(ang) * rx;
    const y = starY + Math.sin(ang) * ry;
    const size = 6.5 * Math.min(1.4, bodyScale);

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
  onEntityClick,
  scene = 'flight',
  isSpaceDock = false,
  planetType,
  onRequestLand,
  onRequestDock
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
    drawScene(
      ctx, w, h, sectorId, systemRef.current, t,
      hitTargetsRef.current, hoverRef.current,
      envRef.current.hazardLevel, envRef.current.radiationLevel
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

  // ---- Body popup lifecycle: anchor vanishes on sector/scene change ----
  useEffect(() => {
    setPopup(null);
  }, [sectorId, scene]);

  // Escape dismisses the popup
  useEffect(() => {
    if (!popup) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPopup(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [popup]);

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
  }, [fetchFailed, system, sectorId, reducedMotion, scene, planetType, isSpaceDock]);

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
    </div>
  );
};

export default SolarSystemViewscreen;
