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
}

interface HitTarget {
  x: number;
  y: number;
  r: number;
  kind: 'planet' | 'station' | 'procedural';
  id?: string;
  name: string;
  lines: string[];
}

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
  system.bodies.forEach((body) => {
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
    const isHovered = !!hover && hover.target.kind !== 'station' &&
      hover.target.name === (body.real ? (body.name || '') : `slot-${body.slot}`);

    // Hit target (real planets are click targets; procedural get flavor hover)
    if (body.real && body.planet_id) {
      const hab = typeof body.habitability === 'number' ? ` — HAB ${Math.round(body.habitability)}%` : '';
      hitTargets.push({
        x, y, r: r + 6, kind: 'planet', id: body.planet_id,
        name: body.name || 'UNKNOWN',
        lines: [
          (body.name || 'UNKNOWN').toUpperCase(),
          `${body.kind.replace(/_/g, ' ').toUpperCase()}${hab}${body.owned ? ' — CLAIMED' : ''}`
        ]
      });
    } else {
      hitTargets.push({
        x, y, r: r + 4, kind: 'procedural',
        name: `slot-${body.slot}`,
        lines: [flavorFor(body.kind)]
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
          if (isHovered) {
            // Subtle selection ring
            ctx.beginPath();
            ctx.arc(x, y, r + 4, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(0, 217, 255, 0.7)';
            ctx.lineWidth = 1;
            ctx.stroke();
          }
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
      lines: [st.name.toUpperCase(), (st.type || 'STATION').replace(/_/g, ' ').toUpperCase()]
    });

    drawables.push({
      y,
      draw: () => drawStationGlyph(ctx, x, y, size, t, idx)
    });
  });

  drawables.sort((a, b) => a.y - b.y);
  drawables.forEach((d) => d.draw());

  drawBelt('front');

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
  onEntityClick
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
  const hoverBoostUntilRef = useRef(0);
  const rafRef = useRef<number | undefined>(undefined);
  const lastDrawRef = useRef(0);
  const reducedMotionRef = useRef(reducedMotion);
  reducedMotionRef.current = reducedMotion;
  const envRef = useRef({ hazardLevel, radiationLevel });
  envRef.current = { hazardLevel, radiationLevel };

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
    drawScene(
      ctx, w, h, sectorId, systemRef.current, t,
      hitTargetsRef.current, hoverRef.current,
      envRef.current.hazardLevel, envRef.current.radiationLevel
    );
  };

  // ---- Fetch the system snapshot on sector change ----
  useEffect(() => {
    let cancelled = false;
    setSystem(null);
    systemRef.current = null;
    setFetchFailed(false);
    hoverRef.current = null;
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
  }, [sectorId]);

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
  }, [fetchFailed, system, sectorId, reducedMotion]);

  // ---- Pointer interaction ----
  const hitTest = (mx: number, my: number): HitTarget | null => {
    let best: HitTarget | null = null;
    let bestDist = Infinity;
    for (const target of hitTargetsRef.current) {
      const dx = mx - target.x;
      const dy = my - target.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= target.r && dist < bestDist) {
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
    canvas.style.cursor = target && target.kind !== 'procedural' ? 'pointer' : 'default';
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

  const handleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onEntityClick) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Hit-test from the click's own coordinates rather than trusting hoverRef
    // (which is stale on touch — there is no mousemove before a tap).
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const target = hitTest(mx, my);
    if (!target) return;
    if (target.kind === 'planet' && target.id) {
      onEntityClick({ type: 'planet', id: target.id, name: target.name });
    } else if (target.kind === 'station' && target.id) {
      onEntityClick({ type: 'station', id: target.id, name: target.name });
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
        onClick={handleClick}
      />
    </div>
  );
};

export default SolarSystemViewscreen;
