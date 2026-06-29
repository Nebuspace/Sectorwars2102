/**
 * Vista Engine — canvas2d post-process compositor
 *
 * Pure, deterministic functions; no Math.random, no Date.now, no global state.
 * A single shared offscreen scene buffer is allocated once in mount() and
 * reused every frame.  buildGrainPattern() is called once at mount time (and
 * whenever model.seed changes) — no per-frame ImageData allocation here.
 *
 * Chain applied in postProcess() order:
 *   1. Blit scene from the offscreen buffer to the visible canvas
 *   2. Bloom — quarter-res bright-pass: downscale scene to scratch, blur with
 *        ctx.filter, composite back additively so only bright emissive sources
 *        (windows, lava, glitter) produce visible glow.  Driven by
 *        model.lighting.bloom; skipped entirely for 'low' quality.  Scratch
 *        canvas is allocated once at mount and passed in — no per-frame alloc.
 *   3. Vignette — radial darkness inset from all four edges
 *   4. Split-tone grade — warm highlights / cool shadows keyed to
 *        model.lighting.colorGradeWarmth + optional per-type warmthBias
 *   5. Film grain — intensity INVERSE to model.desirability (hostile = grittier),
 *        tiled from a deterministic seed-driven noise canvas
 */

import { VistaModel } from '../../contract';
import { SeededRng, deriveChildSeed } from '../../core/rng';
import type { TypeGrade } from '../../core/profiles';

// ---------------------------------------------------------------------------
// Grain tile dimensions
// ---------------------------------------------------------------------------

/** Side length (px) of the deterministic noise tile tiled across the canvas. */
const GRAIN_TILE = 128;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Build a deterministic noise tile (GRAIN_TILE×GRAIN_TILE) from model.seed.
 * Pre-compute once at mount time; reuse in postProcess via ctx.createPattern().
 * Rebuild only when model.seed changes — not per-frame.
 *
 * @param model Current VistaModel; only model.seed is consumed.
 * @returns An HTMLCanvasElement suitable for ctx.createPattern('repeat').
 */
export function buildGrainPattern(model: VistaModel): HTMLCanvasElement {
  const tile = document.createElement('canvas');
  tile.width  = GRAIN_TILE;
  tile.height = GRAIN_TILE;
  const tileCtx = tile.getContext('2d')!;
  const img = tileCtx.createImageData(GRAIN_TILE, GRAIN_TILE);
  // Seeded via a child stream of model.seed so the grain is unique per planet
  // but consistent across frames and sessions.
  const rng = new SeededRng(deriveChildSeed(model.seed, 'post-grain'));
  for (let i = 0; i < img.data.length; i += 4) {
    const v = Math.round(rng.next01() * 255);  // 0–255, uniform
    img.data[i]     = v;
    img.data[i + 1] = v;
    img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  tileCtx.putImageData(img, 0, 0);
  return tile;
}

/**
 * Apply the full post-process chain, compositing the pre-rendered offscreen
 * scene buffer onto the visible canvas.
 *
 * Call this at the end of every render cycle, AFTER drawScene() has written
 * to `offscreen`.  The function fully replaces whatever was on `ctx` — call
 * ctx.clearRect first only if an empty base is needed (not required here
 * because step 1 draws the full scene).
 *
 * @param ctx         Visible canvas 2D context (destination)
 * @param offscreen   Scene buffer that drawScene() wrote to (source)
 * @param w           Canvas width (pixels)
 * @param h           Canvas height (pixels)
 * @param model       Current VistaModel (drives grade + grain + bloom parameters)
 * @param grainTile   Pre-built noise tile from buildGrainPattern() — not rebuilt here
 * @param grade       Optional per-type "film stock" from PlanetProfile.grade
 * @param bloomScratch Quarter-res scratch canvas allocated once at mount; omit/null to skip bloom
 * @param quality     View quality tier — 'low' skips bloom entirely; undefined treated as 'high'
 */
export function postProcess(
  ctx: CanvasRenderingContext2D,
  offscreen: HTMLCanvasElement,
  w: number,
  h: number,
  model: VistaModel,
  grainTile: HTMLCanvasElement,
  grade?: TypeGrade,
  bloomScratch?: HTMLCanvasElement | null,
  quality?: 'low' | 'med' | 'high',
): void {
  // 1. Blit scene from offscreen buffer — this replaces any prior frame.
  ctx.clearRect(0, 0, w, h);
  ctx.drawImage(offscreen, 0, 0);

  // 2. Bloom — quarter-res bright-pass; skipped on 'low' quality or near-zero bloom.
  if (bloomScratch && quality !== 'low') {
    applyBloom(ctx, offscreen, bloomScratch, w, h, model.lighting.bloom, quality);
  }

  // 3. Vignette
  applyVignette(ctx, w, h, grade?.vignetteStrength);

  // 4. Split-tone grade: warm highlights / cool shadows.
  //    Composite: model value + per-type bias, clamped to -1…+1.
  const warmth = clamp(
    model.lighting.colorGradeWarmth + (grade?.warmthBias ?? 0),
    -1, 1,
  );
  applySplitTone(ctx, w, h, warmth);

  // 5. Film grain: intensity inverse to desirability — hostile worlds are grittier.
  //    desirability 1.0 (lush) → minimal grain floor; 0.0 (hostile) → full grain.
  const grainBase      = clamp(1 - model.desirability, 0, 1);
  const grainIntensity = (grade?.grainScale ?? 1) * grainBase * 0.07 + 0.008;
  applyGrain(ctx, w, h, grainTile, grainIntensity);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Full-frame radial vignette — darkens edges, leaves centre intact.
 * Uses an elliptical radial gradient (center → corners) for the classic
 * cinematic barrel-mask look.
 */
function applyVignette(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  strength?: number,
): void {
  const s  = clamp(strength ?? 0.55, 0, 1);
  const cx = w * 0.5;
  const cy = h * 0.5;
  // Gradient radii: inner = ~20% of canvas diagonal (sharp center), outer = ~72% (dark corners).
  const diag   = Math.hypot(w, h);
  const rInner = diag * 0.20;
  const rOuter = diag * 0.72;
  const grad = ctx.createRadialGradient(cx, cy, rInner, cx, cy, rOuter);
  grad.addColorStop(0, 'rgba(0, 0, 0, 0)');
  grad.addColorStop(1, `rgba(0, 0, 0, ${s.toFixed(3)})`);
  ctx.save();
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}

/**
 * Split-tone color grade: warms highlights and cools shadows (or vice versa).
 *
 * Canvas-2D approach — two composite passes:
 *   Pass A ('screen'): adds warm/cool tint to bright regions (highlights).
 *     Screen only brightens; dark pixels are mostly unaffected.
 *   Pass B ('multiply'): multiplies by a near-neutral color to shift darks.
 *     Multiply only darkens; bright pixels receive the neutral ~1.0 factor.
 *
 * This produces a visible warm-in-highlights / cool-in-shadows (or reverse)
 * without per-pixel getImageData.
 *
 * @param warmth -1 (cold highlights + warm shadows) … +1 (warm highlights + cool shadows)
 */
function applySplitTone(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  warmth: number,
): void {
  if (Math.abs(warmth) < 0.02) return;  // skip when effectively neutral
  const str = Math.abs(warmth);

  ctx.save();

  if (warmth > 0) {
    // Warm highlights: screen with amber → highlights pick up golden tones.
    ctx.globalCompositeOperation = 'screen';
    ctx.fillStyle = `rgba(${Math.round(95 * str)}, ${Math.round(50 * str)}, 0, ${(str * 0.13).toFixed(3)})`;
    ctx.fillRect(0, 0, w, h);
    // Cool shadows: multiply with near-white + subtle blue-shift → darks cool slightly.
    ctx.globalCompositeOperation = 'multiply';
    ctx.fillStyle = `rgba(248, 250, ${Math.round(255 - str * 6)}, 1)`;
    ctx.fillRect(0, 0, w, h);
  } else {
    // Cold highlights: screen with steel-blue → bright regions chill.
    ctx.globalCompositeOperation = 'screen';
    ctx.fillStyle = `rgba(0, ${Math.round(28 * str)}, ${Math.round(90 * str)}, ${(str * 0.13).toFixed(3)})`;
    ctx.fillRect(0, 0, w, h);
    // Slightly warm shadows: multiply with near-white + subtle amber-shift → darks warm.
    ctx.globalCompositeOperation = 'multiply';
    ctx.fillStyle = `rgba(${Math.round(255 - str * 4)}, 252, 248, 1)`;
    ctx.fillRect(0, 0, w, h);
  }

  ctx.restore();
}

/**
 * Film grain pass: tile the pre-built noise pattern over the frame.
 * 'overlay' composite preserves scene contrast while adding texture;
 * at low alpha the grain reads as fine analogue noise, not posterization.
 *
 * @param intensity 0 (no grain) … ~0.08 (heavy grain on hostile worlds)
 */
function applyGrain(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  grainTile: HTMLCanvasElement,
  intensity: number,
): void {
  if (intensity < 0.004) return;
  const pattern = ctx.createPattern(grainTile, 'repeat');
  if (!pattern) return;
  ctx.save();
  ctx.globalCompositeOperation = 'overlay';
  ctx.globalAlpha = clamp(intensity, 0, 1);
  ctx.fillStyle = pattern;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}

/**
 * Quarter-res bright-pass bloom using ctx.filter blur + additive composite.
 *
 * Algorithm:
 *   1. Downscale the offscreen scene to bloomScratch (quarter w × quarter h).
 *      Small target lets the CSS filter blur spread at much lower pixel cost
 *      than blurring the full-res canvas.
 *   2. Draw bloomScratch back at full size with a blur filter + 'lighter'
 *      (additive) composite.  Dark pixels ≈ 0 → near-zero additive contribution,
 *      so only bright emissive sources (windows, lava glints, glitter) produce
 *      visible glow — the implicit bright-pass.
 *
 * Deterministic: no Math.random, no Date.now.  No getImageData.
 * bloomScratch must be allocated once at mount time (caller's responsibility).
 *
 * @param quality  'med' reduces effective intensity ~40 % vs 'high'/undefined.
 *                 'low' must be gated at the postProcess call site — not re-checked here.
 */
function applyBloom(
  ctx: CanvasRenderingContext2D,
  offscreen: HTMLCanvasElement,
  bloomScratch: HTMLCanvasElement,
  w: number,
  h: number,
  bloomIntensity: number,
  quality?: 'low' | 'med' | 'high',
): void {
  // Scale back intensity for medium quality.
  const effective = quality === 'med' ? bloomIntensity * 0.60 : bloomIntensity;
  if (effective < 0.04) return;

  const bw = bloomScratch.width;
  const bh = bloomScratch.height;

  // Step 1 — downscale scene to quarter-res scratch.
  const bCtx = bloomScratch.getContext('2d');
  if (!bCtx) return;
  bCtx.clearRect(0, 0, bw, bh);
  bCtx.drawImage(offscreen, 0, 0, bw, bh);

  // Step 2 — blit back at full size: blur spreads the glow; 'lighter' is additive.
  // Blur radius in quarter-res pixels scales with intensity for a wider spread at
  // high bloom.  At 1/4 res these values correspond to 32–96 px at full resolution.
  const blurPx = Math.round(8 + effective * 16);
  ctx.save();
  ctx.filter                   = `blur(${blurPx}px)`;
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha              = clamp(effective * 0.60, 0, 0.80);
  ctx.drawImage(bloomScratch, 0, 0, bw, bh, 0, 0, w, h);
  ctx.restore();
}
