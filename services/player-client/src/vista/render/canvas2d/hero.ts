/**
 * Vista Engine — hero-landform renderer (WO-VISTA-TK1)
 *
 * The single dominant midground focal feature — "the biggest AAA win" per
 * the vista-aaa-brief design directive. Six per-biome shapes, each drawn as
 * a directionally-shaded silhouette using the SAME volumetric primitives
 * `drawLandmarks` (backend.ts) already established for the smaller
 * background landmarks: shadeFlank (lit/shadow flank split) + rimLight
 * (sun-relative silhouette edge) + aoPool (contact-shadow footprint).
 *
 * BACKEND-SPLIT (vista-aaa-brief §roadmap NOTE): this is a new per-layer
 * module, not an addition to backend.ts's 8k+ lines — the toolkit work is
 * the natural moment to start the split, done as-we-go per the brief.
 *
 * geom is null whenever model.layers.hero is absent (all non-hero-shape
 * types — profiles.ts's PlanetProfile.heroLandform) — draw() is a no-op.
 */

import { shadeFlank, rimLight, aoPool, type LightingModel } from './lighting';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * Pre-baked screen geometry for the hero landform. Built once in
 * backend.ts's buildVistaCache from model.layers.hero; owned here since
 * this module is the sole consumer of the shape-specific draw logic.
 */
export type HeroGeom = {
  shape: string;        // 'cone' | 'glacier' | 'sea-stack' | 'mesa' | 'massif' | 'delta-bluff'
  cx: number;            // centre x on screen
  baseY: number;         // y of the terrain ground line (hero rises upward from here)
  height: number;        // total hero height in px
  width: number;         // base half-width in px
  fillColor: string;     // main silhouette fill ('rgba(…)' CSS string)
  accentColor: string;   // rim / sheen / snow-cap accent color
};

// ---------------------------------------------------------------------------
// Shared per-flank shading helper — mirrors drawLandmarks's repeated pattern
// ---------------------------------------------------------------------------

function flanks(lighting: LightingModel) {
  const rightShade = shadeFlank(lighting, 0);
  const leftShade  = shadeFlank(lighting, 180);
  const litIsRight = rightShade.mult > leftShade.mult;
  return { rightShade, leftShade, litIsRight };
}

// ---------------------------------------------------------------------------
// drawHeroLandform — public entry point
// ---------------------------------------------------------------------------

/**
 * Draw the hero landform. No-op when geom is null (non-hero-shape types).
 *
 * @param ctx      Canvas 2D rendering context
 * @param geom     Pre-baked hero geometry (null = nothing to draw)
 * @param lighting model.lighting block (shadeFlank/rimLight/aoPool input)
 * @param bright   Day-cycle brightness 0..1 (dc.bright) — modulates silhouette darkness
 */
export function drawHeroLandform(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom | null,
  lighting: LightingModel,
  bright: number,
): void {
  if (!geom) return;

  const { cx, baseY, width } = geom;
  const brightK = 0.7 + bright * 0.3;
  const { rightShade, leftShade, litIsRight } = flanks(lighting);

  ctx.save();

  // Contact-shadow footprint — sits beneath the geometry, drawn first.
  aoPool(ctx, cx, baseY, width * 0.75, lighting);

  ctx.globalAlpha = brightK;

  switch (geom.shape) {
    case 'cone':
      drawCone(ctx, geom, lighting, brightK, rightShade, leftShade, litIsRight);
      break;
    case 'glacier':
      drawGlacier(ctx, geom, lighting, brightK, bright);
      break;
    case 'mesa':
      drawMesa(ctx, geom, lighting, brightK, bright, rightShade, leftShade, litIsRight);
      break;
    case 'massif':
      drawMassif(ctx, geom, lighting, brightK, rightShade, leftShade, litIsRight);
      break;
    case 'sea-stack':
      drawSeaStack(ctx, geom, lighting, brightK, rightShade, leftShade, litIsRight);
      break;
    case 'delta-bluff':
      drawDeltaBluff(ctx, geom, brightK, rightShade, leftShade, litIsRight);
      break;
    default:
      // Unknown shape → skip (degrade gracefully, never throw).
      break;
  }

  ctx.restore();
}

// ---------------------------------------------------------------------------
// cone — VOLCANIC stratovolcano.  Two-flank triangle + apex crater glow.
// Larger sibling of drawLandmarks's 'cone' case (same shading technique).
// ---------------------------------------------------------------------------

function drawCone(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom,
  lighting: LightingModel,
  brightK: number,
  rightShade: ReturnType<typeof shadeFlank>,
  leftShade: ReturnType<typeof shadeFlank>,
  litIsRight: boolean,
): void {
  const { cx, baseY, height, width, fillColor, accentColor } = geom;
  ctx.fillStyle = fillColor;

  // Shadow-side flank
  ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx - width, baseY); ctx.lineTo(cx, baseY); ctx.lineTo(cx, baseY - height);
  } else {
    ctx.moveTo(cx, baseY - height); ctx.lineTo(cx + width, baseY); ctx.lineTo(cx, baseY);
  }
  ctx.closePath();
  ctx.fill();

  // Lit flank
  ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx, baseY - height); ctx.lineTo(cx + width, baseY); ctx.lineTo(cx, baseY);
  } else {
    ctx.moveTo(cx - width, baseY); ctx.lineTo(cx, baseY); ctx.lineTo(cx, baseY - height);
  }
  ctx.closePath();
  ctx.fill();

  // Key-colour tint on the lit face
  const litTint = litIsRight ? rightShade.tint : leftShade.tint;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = brightK * 0.12;
  ctx.fillStyle = litTint;
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx, baseY - height); ctx.lineTo(cx + width, baseY); ctx.lineTo(cx, baseY);
  } else {
    ctx.moveTo(cx - width, baseY); ctx.lineTo(cx, baseY); ctx.lineTo(cx, baseY - height);
  }
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  // Sun-relative rim on the lit flank's outer edge
  const rim = rimLight(lighting, litIsRight ? 0 : 180, 'sun');
  if (rim.mult > 0.005) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = brightK * rim.mult * 1.3;
    ctx.strokeStyle = rim.tint;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    if (litIsRight) {
      ctx.moveTo(cx, baseY - height); ctx.lineTo(cx + width, baseY);
    } else {
      ctx.moveTo(cx - width, baseY); ctx.lineTo(cx, baseY - height);
    }
    ctx.stroke();
    ctx.restore();
  }

  ctx.globalAlpha = brightK;

  // Caldera glow — the hero cone always carries the accent (it IS the volcano).
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = 0.42;
  const cg = ctx.createRadialGradient(cx, baseY - height, 0, cx, baseY - height, width * 0.5);
  cg.addColorStop(0, accentColor);
  cg.addColorStop(1, 'rgba(255, 60, 0, 0)');
  ctx.fillStyle = cg;
  ctx.fillRect(cx - width * 0.5, baseY - height - width * 0.5, width, width);
  // Crater dot
  ctx.globalAlpha = 0.6;
  ctx.fillStyle = accentColor;
  ctx.beginPath();
  ctx.arc(cx, baseY - height, Math.max(3, width * 0.05), 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// glacier — ICE.  Sky-facing wedge, larger sibling of drawLandmarks's
// 'glacier' case — top surface driven by the 270°-azimuth (upward) shading.
// ---------------------------------------------------------------------------

function drawGlacier(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom,
  lighting: LightingModel,
  brightK: number,
  bright: number,
): void {
  const { cx, baseY, height, width, accentColor, fillColor } = geom;
  const gw = width * 1.1;
  const gh = height * 0.72;
  const topShade = shadeFlank(lighting, 270);

  ctx.globalAlpha = brightK * topShade.mult;
  ctx.fillStyle = fillColor;
  ctx.beginPath();
  ctx.moveTo(cx - gw,        baseY);
  ctx.lineTo(cx - gw * 0.42, baseY - gh);
  ctx.lineTo(cx + gw * 0.18, baseY - gh * 1.08);   // secondary serac shoulder
  ctx.lineTo(cx + gw * 0.60, baseY - gh * 0.34);
  ctx.lineTo(cx + gw,        baseY);
  ctx.closePath();
  ctx.fill();

  ctx.globalAlpha = brightK;

  // Ice sheen — soft lighter edge along the upper ridge (always on; this IS the glacier)
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = bright * 0.30 + 0.06;
  ctx.strokeStyle = accentColor;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(cx - gw * 0.42, baseY - gh);
  ctx.lineTo(cx + gw * 0.18, baseY - gh * 1.08);
  ctx.lineTo(cx + gw * 0.60, baseY - gh * 0.34);
  ctx.stroke();
  ctx.restore();

  // Crevasse shadows — a few short dark diagonal strokes across the face
  ctx.save();
  ctx.globalAlpha = 0.20 * brightK;
  ctx.strokeStyle = 'rgba(40, 70, 95, 0.8)';
  ctx.lineWidth = 1.5;
  for (let i = 0; i < 4; i++) {
    const t = 0.2 + i * 0.18;
    const x0 = cx - gw * 0.30 + gw * t * 0.9;
    const y0 = baseY - gh * (0.15 + t * 0.55);
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x0 + gw * 0.10, y0 + gh * 0.10);
    ctx.stroke();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// mesa — BARREN.  Wide flat-topped butte, larger sibling of drawLandmarks's
// 'mesa' case — sloping sides split left/right, top lit by sky-facing shade.
// ---------------------------------------------------------------------------

function drawMesa(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom,
  lighting: LightingModel,
  brightK: number,
  bright: number,
  rightShade: ReturnType<typeof shadeFlank>,
  leftShade: ReturnType<typeof shadeFlank>,
  litIsRight: boolean,
): void {
  const { cx, baseY, height, width, fillColor } = geom;
  const topW = width * 0.82;
  const h2   = height * 0.70;
  const topShade = shadeFlank(lighting, 270);

  ctx.fillStyle = fillColor;

  ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx - width, baseY); ctx.lineTo(cx - topW, baseY - h2);
    ctx.lineTo(cx, baseY - h2); ctx.lineTo(cx, baseY);
  } else {
    ctx.moveTo(cx, baseY); ctx.lineTo(cx, baseY - h2);
    ctx.lineTo(cx + topW, baseY - h2); ctx.lineTo(cx + width, baseY);
  }
  ctx.closePath();
  ctx.fill();

  ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx, baseY); ctx.lineTo(cx, baseY - h2);
    ctx.lineTo(cx + topW, baseY - h2); ctx.lineTo(cx + width, baseY);
  } else {
    ctx.moveTo(cx - width, baseY); ctx.lineTo(cx - topW, baseY - h2);
    ctx.lineTo(cx, baseY - h2); ctx.lineTo(cx, baseY);
  }
  ctx.closePath();
  ctx.fill();

  // Layered sedimentary strata — 2 subtle horizontal seams on the lit face
  ctx.save();
  ctx.globalAlpha = 0.16 * brightK;
  ctx.strokeStyle = 'rgba(40, 30, 20, 0.7)';
  ctx.lineWidth = 1.5;
  for (let i = 1; i <= 2; i++) {
    const sy = baseY - h2 * (i / 3);
    const sw = topW + (width - topW) * (1 - i / 3);
    ctx.beginPath();
    ctx.moveTo(cx - sw, sy); ctx.lineTo(cx + sw, sy);
    ctx.stroke();
  }
  ctx.restore();

  // Pale top-face rim highlight
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = bright * topShade.mult * 0.18;
  ctx.strokeStyle = 'rgba(220, 210, 190, 0.75)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx - topW, baseY - h2); ctx.lineTo(cx + topW, baseY - h2);
  ctx.stroke();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// massif — MOUNTAINOUS.  Dominant Matterhorn-style peak: asymmetric main
// summit + a lower shoulder peak, with a solid snow-cap (not a thin outline).
// ---------------------------------------------------------------------------

function drawMassif(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom,
  lighting: LightingModel,
  brightK: number,
  rightShade: ReturnType<typeof shadeFlank>,
  leftShade: ReturnType<typeof shadeFlank>,
  litIsRight: boolean,
): void {
  const { cx, baseY, height, width, fillColor } = geom;
  // Asymmetric summit: apex offset toward the lit side for a jagged, non-toy-triangle read.
  const apexX = litIsRight ? cx + width * 0.12 : cx - width * 0.12;
  // Shoulder peak on the opposite (shadow) side — lower and set back.
  const shoulderX = litIsRight ? cx - width * 0.55 : cx + width * 0.55;
  const shoulderY = baseY - height * 0.58;

  ctx.fillStyle = fillColor;

  // Shadow-side flank (main summit → shoulder)
  ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
  ctx.beginPath();
  ctx.moveTo(litIsRight ? cx - width : cx + width, baseY);
  ctx.lineTo(shoulderX, shoulderY);
  ctx.lineTo(apexX, baseY - height);
  ctx.lineTo(cx, baseY);
  ctx.closePath();
  ctx.fill();

  // Lit-side flank (main summit → base)
  ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
  ctx.beginPath();
  ctx.moveTo(apexX, baseY - height);
  ctx.lineTo(litIsRight ? cx + width : cx - width, baseY);
  ctx.lineTo(cx, baseY);
  ctx.closePath();
  ctx.fill();

  // Rim light along the lit summit ridge
  const rim = rimLight(lighting, litIsRight ? 0 : 180, 'sun');
  if (rim.mult > 0.005) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = brightK * rim.mult * 1.2;
    ctx.strokeStyle = rim.tint;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(apexX, baseY - height);
    ctx.lineTo(litIsRight ? cx + width : cx - width, baseY);
    ctx.stroke();
    ctx.restore();
  }

  ctx.globalAlpha = brightK;

  // Solid snow-cap — filled polygon (not an outline), covering the top ~22% of the summit.
  const capFrac = 0.22;
  const capY    = baseY - height * (1 - capFrac);
  const capHalfAtY = width * capFrac * 0.9;
  ctx.save();
  ctx.globalAlpha = brightK * 0.94;
  ctx.fillStyle = 'rgba(240, 246, 252, 0.95)';
  ctx.beginPath();
  ctx.moveTo(apexX, baseY - height);
  ctx.lineTo(apexX - capHalfAtY, capY);
  ctx.lineTo(apexX + capHalfAtY, capY);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  // Alpenglow tint on the snow-cap's lit edge
  const capTint = litIsRight ? rightShade.tint : leftShade.tint;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = brightK * 0.20;
  ctx.fillStyle = capTint;
  ctx.beginPath();
  ctx.moveTo(apexX, baseY - height);
  ctx.lineTo(apexX + (litIsRight ? capHalfAtY : -capHalfAtY), capY);
  ctx.lineTo(apexX, capY);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// sea-stack — OCEANIC.  Tall eroded rock pillar rising from the water; a
// wider-based, rounded-top cousin of drawLandmarks's 'spire' case.
// ---------------------------------------------------------------------------

function drawSeaStack(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom,
  lighting: LightingModel,
  brightK: number,
  rightShade: ReturnType<typeof shadeFlank>,
  leftShade: ReturnType<typeof shadeFlank>,
  litIsRight: boolean,
): void {
  const { cx, baseY, height, width, fillColor, accentColor } = geom;
  const sw = width * 0.42;         // base half-width — wider than a spire, reads as rock, not a mast
  const midW = sw * 0.72;          // erosion taper mid-column
  const topY = baseY - height;

  ctx.fillStyle = fillColor;

  // Shadow half
  ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx - sw, baseY);
    ctx.lineTo(cx - midW, baseY - height * 0.55);
    ctx.lineTo(cx - sw * 0.5, topY);
    ctx.lineTo(cx, topY);
    ctx.lineTo(cx, baseY);
  } else {
    ctx.moveTo(cx, topY);
    ctx.lineTo(cx + sw * 0.5, topY);
    ctx.lineTo(cx + midW, baseY - height * 0.55);
    ctx.lineTo(cx + sw, baseY);
    ctx.lineTo(cx, baseY);
  }
  ctx.closePath();
  ctx.fill();

  // Lit half
  ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx, topY);
    ctx.lineTo(cx + sw * 0.5, topY);
    ctx.lineTo(cx + midW, baseY - height * 0.55);
    ctx.lineTo(cx + sw, baseY);
    ctx.lineTo(cx, baseY);
  } else {
    ctx.moveTo(cx - sw, baseY);
    ctx.lineTo(cx - midW, baseY - height * 0.55);
    ctx.lineTo(cx - sw * 0.5, topY);
    ctx.lineTo(cx, topY);
    ctx.lineTo(cx, baseY);
  }
  ctx.closePath();
  ctx.fill();

  // Sun-side rim — thin, since sea-stacks are wet rock, not ice or snow
  const rim = rimLight(lighting, litIsRight ? 0 : 180, 'sun');
  if (rim.mult > 0.005) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = brightK * rim.mult * 1.1;
    ctx.strokeStyle = rim.tint;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    if (litIsRight) {
      ctx.moveTo(cx, topY); ctx.lineTo(cx + sw * 0.5, topY); ctx.lineTo(cx + midW, baseY - height * 0.55);
    } else {
      ctx.moveTo(cx - midW, baseY - height * 0.55); ctx.lineTo(cx - sw * 0.5, topY); ctx.lineTo(cx, topY);
    }
    ctx.stroke();
    ctx.restore();
  }

  ctx.globalAlpha = brightK;

  // Sea-spray sheen — faint additive wash at the waterline base
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = accentColor;
  ctx.beginPath();
  ctx.ellipse(cx, baseY, sw * 1.1, sw * 0.30, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// delta-bluff — TERRAN.  Low, wide, soft-shouldered headland — a golden-hour
// river-delta bluff, not a hard geometric mesa.
// ---------------------------------------------------------------------------

function drawDeltaBluff(
  ctx: CanvasRenderingContext2D,
  geom: HeroGeom,
  brightK: number,
  rightShade: ReturnType<typeof shadeFlank>,
  leftShade: ReturnType<typeof shadeFlank>,
  litIsRight: boolean,
): void {
  const { cx, baseY, height, width, fillColor } = geom;
  // Lower profile than mesa/massif — a headland silhouette, not a peak.
  const bh = height * 0.55;
  const crestW = width * 0.55;

  ctx.fillStyle = fillColor;

  // Shadow half — rounded shoulder via quadratic curve, not a hard angle
  ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx - width, baseY);
    ctx.quadraticCurveTo(cx - width * 0.7, baseY - bh * 0.9, cx - crestW, baseY - bh);
    ctx.lineTo(cx, baseY - bh);
    ctx.lineTo(cx, baseY);
  } else {
    ctx.moveTo(cx, baseY);
    ctx.lineTo(cx, baseY - bh);
    ctx.lineTo(cx + crestW, baseY - bh);
    ctx.quadraticCurveTo(cx + width * 0.7, baseY - bh * 0.9, cx + width, baseY);
  }
  ctx.closePath();
  ctx.fill();

  // Lit half
  ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
  ctx.beginPath();
  if (litIsRight) {
    ctx.moveTo(cx, baseY);
    ctx.lineTo(cx, baseY - bh);
    ctx.lineTo(cx + crestW, baseY - bh);
    ctx.quadraticCurveTo(cx + width * 0.7, baseY - bh * 0.9, cx + width, baseY);
  } else {
    ctx.moveTo(cx - width, baseY);
    ctx.quadraticCurveTo(cx - width * 0.7, baseY - bh * 0.9, cx - crestW, baseY - bh);
    ctx.lineTo(cx, baseY - bh);
    ctx.lineTo(cx, baseY);
  }
  ctx.closePath();
  ctx.fill();

  // Golden-hour rim catch-light along the crest — warm, low-angle grazing light
  const litTint = litIsRight ? rightShade.tint : leftShade.tint;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = brightK * 0.16;
  ctx.strokeStyle = litTint;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(cx - crestW, baseY - bh);
  ctx.lineTo(cx + crestW, baseY - bh);
  ctx.stroke();
  ctx.restore();
}
