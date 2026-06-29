/**
 * Vista Engine — canvas2d directional lighting helpers
 *
 * Pure, deterministic helpers that consume model.lighting and return per-face
 * shading data for backend draw functions.  No Math.random, Date.now, global
 * state, or DOM access — every function is safe to call from a unit test.
 *
 * Integration sketch (backend draw functions):
 *
 *   // Right flank of a landmark (normal points ~east, azimuth 90°):
 *   const right = shadeFlank(model.lighting, 90);
 *   ctx.globalAlpha = lm.baseAlpha * right.mult;
 *   ctx.fillStyle = rightFlankColor;
 *   ctx.fill();
 *   // Additive key-color tint on the lit face:
 *   ctx.save();
 *   ctx.globalCompositeOperation = 'lighter';
 *   ctx.globalAlpha = 0.12;
 *   ctx.fillStyle = right.tint;
 *   ctx.fill();
 *   ctx.restore();
 *
 * Azimuth convention (screen space, matches pipeline.ts sunAzimuth):
 *   0° = screen-right (+x), 90° = screen-down (+y),
 *   180° = screen-left, 270° = screen-up.
 *   Face normals are expressed in the same convention.
 */

import type { RGB } from '../../contract';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * The model.lighting block shape.  Matches VistaModel['lighting'] exactly;
 * defined here so callers can import without pulling in all of contract.ts.
 */
export interface LightingModel {
  /** Sun azimuth + elevation [azDeg 0–360, elevDeg −90–90]. */
  keyDir: [number, number];
  keyColor: RGB;
  /** 0–1 ceiling from star luminosity + desirability. */
  keyIntensity: number;
  /** Soft ambient tint from sky horizon bounce. */
  ambient: RGB;
  /** Cool dim counterpoint from the sky dome. */
  fill: RGB;
  /** 0–1; rises with desirability. */
  bloom: number;
  /** −1 (cold/blue) … +1 (warm/golden). */
  colorGradeWarmth: number;
}

/** Returned by shadeFlank and rimLight. */
export interface FlankResult {
  /**
   * Brightness multiplier for this face, 0–1.
   * Multiply against ctx.globalAlpha or use to darken the fill color before
   * drawing the face geometry.
   */
  mult: number;
  /**
   * Additive tint overlay as a CSS rgba() string.
   * Draw with globalCompositeOperation='lighter' at low alpha on lit faces
   * to shift the color temperature toward the key light.
   */
  tint: string;
}

// ---------------------------------------------------------------------------
// Internal math helpers
// ---------------------------------------------------------------------------

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

const DEG2RAD = Math.PI / 180;

/**
 * Signed angular difference in degrees, wrapped to (−180, 180].
 * Positive = normalAz is CCW from keyAz in screen space.
 */
function deltaAzDeg(normalAzDeg: number, keyAzDeg: number): number {
  let d = ((normalAzDeg - keyAzDeg) % 360 + 360) % 360;
  if (d > 180) d -= 360;
  return d;
}

/**
 * Lambert dot product for a face with outward-normal azimuth `normalAzimuthDeg`
 * lit by a directional source coming from `keyAzDeg` at elevation `keyElevDeg`.
 *
 * Returns 0–1:  1 = fully front-lit, 0 = terminator or shadow side.
 */
function lambertAz(normalAzimuthDeg: number, keyAzDeg: number, keyElevDeg: number): number {
  const deltaAz = deltaAzDeg(normalAzimuthDeg, keyAzDeg) * DEG2RAD;
  const hDot = Math.cos(deltaAz);                                // −1 … +1

  // At higher elevations the horizontal component matters less; at grazing
  // elevations (near 0°) it dominates.  vFactor stays ≥ 0.1 so a near-horizon
  // sun still contributes rather than collapsing to zero.
  const elevR   = clamp(keyElevDeg, -90, 90) * DEG2RAD;
  const vFactor = Math.max(0.1, Math.cos(elevR));

  return clamp(hDot * vFactor, 0, 1);
}

/** Average brightness of an RGB triple as a 0–1 scalar. */
function rgbBrightness(c: RGB): number {
  return (c[0] + c[1] + c[2]) / (3 * 255);
}

/** Format an RGB triple + alpha as a CSS rgba() string. */
function rgbaStr(c: RGB, a: number): string {
  return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${clamp(a, 0, 1).toFixed(3)})`;
}

// ---------------------------------------------------------------------------
// shadeFlank — primary per-face directional shading
// ---------------------------------------------------------------------------

/**
 * Compute the brightness multiplier and additive key-light tint for a face
 * whose outward normal points `normalAzimuthDeg` degrees in screen space.
 *
 * Contract:
 *   shadeFlank(lighting, azimuth toward keyDir).mult
 *     > shadeFlank(lighting, azimuth away from keyDir).mult
 *
 *   shadeFlank(lighting, az).mult is always > 0 (ambient lift prevents
 *   shadow sides from being crushed to black).
 *
 * Usage:
 *   - Multiply `mult` against ctx.globalAlpha when drawing the face.
 *   - Draw `tint` over the face with 'lighter' at low alpha to tint lit
 *     surfaces toward the key light color.
 *
 * @param lighting         model.lighting block
 * @param normalAzimuthDeg outward face-normal direction in screen degrees
 */
export function shadeFlank(lighting: LightingModel, normalAzimuthDeg: number): FlankResult {
  const [keyAzDeg, keyElevDeg] = lighting.keyDir;
  const lambert = lambertAz(normalAzimuthDeg, keyAzDeg, keyElevDeg);

  // Shadow floor: ambient + fill ensure shadow sides never go fully black.
  // ambient weight 0.65, fill weight 0.25, total clamped to a realistic floor.
  const ambientFloor = rgbBrightness(lighting.ambient) * 0.65
                     + rgbBrightness(lighting.fill)    * 0.25;
  const floor = clamp(ambientFloor, 0.12, 0.55);

  // Key pass: front-lit faces ramp up from the floor toward full brightness.
  const keyK = clamp(lighting.keyIntensity, 0, 1);
  const mult  = floor + (1 - floor) * lambert * keyK;

  // Tint: lit faces get a key-color overlay.  Shadow-side tint is zero
  // (returning `rgba(..., 0)` is deliberate — no color shift in shadow).
  const tintAlpha = lambert * keyK * 0.22;
  const tint = rgbaStr(lighting.keyColor, tintAlpha);

  return { mult, tint };
}

// ---------------------------------------------------------------------------
// shadowLift — shadow-side ambient/fill lift overlay
// ---------------------------------------------------------------------------

/**
 * Returns a CSS rgba() color that lifts a shadow-side face using the ambient
 * and fill lights.  Apply over the base fill with 'source-over' to prevent
 * crushed blacks on heavily shadowed geometry.
 *
 * @param lighting    model.lighting block
 * @param shadowDepth 0 (lit, no lift) … 1 (full shadow, maximum lift)
 */
export function shadowLift(lighting: LightingModel, shadowDepth: number): string {
  const depth = clamp(shadowDepth, 0, 1);
  // Blend ambient (dominant sky scatter) with fill (cool dome secondary).
  const [ar, ag, ab] = lighting.ambient;
  const [fr, fg, fb] = lighting.fill;
  const t = 0.65;  // ambient weight
  const r = Math.round(ar * t + fr * (1 - t));
  const g = Math.round(ag * t + fg * (1 - t));
  const b = Math.round(ab * t + fb * (1 - t));
  // Maximum 35% opacity: lifts shadows into the ambient range while keeping
  // silhouettes readable.
  const alpha = depth * 0.35;
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
}

// ---------------------------------------------------------------------------
// keyTint — key-pass color overlay for lit faces
// ---------------------------------------------------------------------------

/**
 * Returns the key light color as a CSS rgba() overlay scaled by keyIntensity.
 * Use with 'lighter' compositing to add a warm/colored glow on sun-facing
 * surfaces.  Typical alpha: 0.05–0.18 on stone/rock, 0.02–0.08 on vegetation.
 *
 * @param lighting model.lighting block
 * @param alpha    base alpha before keyIntensity scaling (caller controls range)
 */
export function keyTint(lighting: LightingModel, alpha: number): string {
  const scaled = clamp(alpha * clamp(lighting.keyIntensity, 0, 1), 0, 1);
  return rgbaStr(lighting.keyColor, scaled);
}

// ---------------------------------------------------------------------------
// rimLight — sun-relative rim + cool fill-edge
// ---------------------------------------------------------------------------

/**
 * Compute a rim-light contribution for faces near the silhouette edge relative
 * to the sun.  Faces ~75° off from keyDir catch the hottest rim; the opposite
 * silhouette edge catches a cool fill rim for depth separation.
 *
 * Usage: apply `mult` as an additive alpha boost on the face geometry and draw
 * `tint` over it with 'lighter' at low alpha.
 *
 * @param lighting          model.lighting block
 * @param normalAzimuthDeg  outward face-normal direction in screen degrees
 * @param side              'sun' = hot rim on key-light side (default) |
 *                          'fill' = cool rim on the opposite silhouette edge
 */
export function rimLight(
  lighting: LightingModel,
  normalAzimuthDeg: number,
  side: 'sun' | 'fill' = 'sun',
): FlankResult {
  const [keyAzDeg, keyElevDeg] = lighting.keyDir;
  const keyK = clamp(lighting.keyIntensity, 0, 1);

  if (side === 'sun') {
    // Hot rim: bell curve peaking at 75° off from the key direction.
    const delta = Math.abs(deltaAzDeg(normalAzimuthDeg, keyAzDeg));
    const rimFactor = clamp(1 - Math.abs(delta - 75) / 30, 0, 1);
    // Elevation boosts: low sun grazes silhouettes more dramatically.
    const elevBoost = clamp(Math.sin(clamp(keyElevDeg, 0, 90) * DEG2RAD) + 0.3, 0.1, 1);
    const intensity = rimFactor * elevBoost * keyK;
    return {
      mult: intensity * 0.22,
      tint: rgbaStr(lighting.keyColor, intensity * 0.18),
    };
  } else {
    // Cool fill rim: opposite silhouette edge, using fill light color.
    const fillAzDeg = (keyAzDeg + 180) % 360;
    const delta = Math.abs(deltaAzDeg(normalAzimuthDeg, fillAzDeg));
    const rimFactor = clamp(1 - Math.abs(delta - 75) / 30, 0, 1);
    const intensity = rimFactor * 0.14;
    return {
      mult: intensity * 0.12,
      tint: rgbaStr(lighting.fill, intensity * 0.12),
    };
  }
}

// ---------------------------------------------------------------------------
// aoPool — fake ambient occlusion contact shadow at object bases
// ---------------------------------------------------------------------------

/**
 * Draws an elliptical AO darkening pool at the base of an object.
 * The pool shifts slightly toward the shadow side of the key light and scales
 * with keyIntensity (hard directional light = deeper contact shadows).
 *
 * Call BEFORE drawing the object fill so the AO sits beneath the geometry.
 * Does not call ctx.save/ctx.restore — caller is responsible for any state
 * it needs to preserve.
 *
 * @param ctx     Canvas 2D rendering context
 * @param x       Object base centre x (pixels)
 * @param y       Object base centre y (pixels)
 * @param r       Object approximate ground-contact radius (pixels)
 * @param lighting model.lighting block
 */
export function aoPool(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  lighting: LightingModel,
): void {
  const [keyAzDeg] = lighting.keyDir;
  // Pool shifts away from the key light toward the shadow side.
  const shadowAzRad = ((keyAzDeg + 180) % 360) * DEG2RAD;
  const offsetX = Math.cos(shadowAzRad) * r * 0.18;
  const cx = x + offsetX;
  const cy = y + r * 0.08;  // slight downward drop (gravity reads true)

  const rx = r * 0.90;
  const ry = r * 0.22;       // flat contact-shadow ellipse

  // Strong key light casts deeper, tighter contact shadows.
  const darkness = clamp(0.18 + clamp(lighting.keyIntensity, 0, 1) * 0.22, 0.15, 0.40);

  // Build the gradient in transformed (squished) coordinate space so the
  // radial gradient appears elliptical in screen space.
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(1, ry / rx);

  const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, rx);
  grad.addColorStop(0,   `rgba(0, 0, 0, ${darkness.toFixed(3)})`);
  grad.addColorStop(0.5, `rgba(0, 0, 0, ${(darkness * 0.45).toFixed(3)})`);
  grad.addColorStop(1,   'rgba(0, 0, 0, 0)');

  ctx.beginPath();
  ctx.arc(0, 0, rx, 0, Math.PI * 2);
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// applyGroundVignette — edges/corners of the ground plane
// ---------------------------------------------------------------------------

/**
 * Darkens the bottom corners and edges of the ground plane.  Strength is
 * inversely proportional to ambient brightness: bright open worlds carry a
 * lighter vignette; dark hostile worlds a deeper one.
 *
 * Call after the ground fill, before scatter instances, so the vignette sits
 * above the raw ground colour but below the scene elements.
 *
 * @param ctx      Canvas 2D rendering context
 * @param w        Canvas width (pixels)
 * @param h        Canvas height (pixels)
 * @param horizonY Horizon Y coordinate (pixels) — top of the ground band
 * @param lighting model.lighting block
 */
export function applyGroundVignette(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  horizonY: number,
  lighting: LightingModel,
): void {
  const ambientBright = rgbBrightness(lighting.ambient);
  // More ambient = lighter vignette.  Clamp so it's always at least subtle.
  const strength = clamp((0.55 - ambientBright) * 1.2, 0.06, 0.38);

  // Radiate from the bottom-centre of the canvas outward to the corners.
  const cx     = w * 0.5;
  const cy     = h;
  const rInner = Math.hypot(w * 0.5, h - horizonY) * 0.10;
  const rOuter = Math.hypot(w * 0.5, h - horizonY) * 1.05;

  const grad = ctx.createRadialGradient(cx, cy, rInner, cx, cy, rOuter);
  grad.addColorStop(0, 'rgba(0, 0, 0, 0)');
  grad.addColorStop(1, `rgba(0, 0, 0, ${strength.toFixed(3)})`);

  ctx.save();
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = grad;
  ctx.fillRect(0, horizonY, w, h - horizonY);
  ctx.restore();
}
