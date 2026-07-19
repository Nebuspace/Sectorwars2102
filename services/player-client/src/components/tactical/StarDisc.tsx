import { useEffect, useRef } from 'react';
import * as THREE from 'three';

import type { TableauFxHarness } from './tableauFxHarness';
import {
  STAR_VERTEX_SHADER,
  STAR_FRAGMENT_SHADER,
  starVisualParams,
  type StarVisualParams,
} from './starShader';

/** The codebase's own nominal fallback px-per-1em (WindshieldTableau.tsx's
 *  own `DEFAULT_REM_PX`) -- used only until a caller has a live-measured
 *  `bandBox.remPx`. RENDERING (this component) always wants the LIVE value
 *  when one is available -- never the fixed `REFERENCE_BAND` (that's
 *  reserved for %-space range-GATING, e.g. dock/land distance checks; see
 *  windshieldTableauLayout.ts:394's own doc-comment on the split) -- this
 *  is just the same safe pre-measurement default every sibling renderer in
 *  this file family already falls back to. */
const DEFAULT_REM_PX = 16;

/** A stable per-star seed so two co-mounted suns (binary systems --
 *  celestial_service.py's `BINARY_CHANCE`) don't pulse/flare/CME in
 *  lockstep. Hashes kind+color (no `Math.random` -- matches StarField.tsx's
 *  own deterministic `hash01` convention) so it's stable across remounts
 *  without every caller having to thread an explicit seed prop. */
function defaultSeed(kind: string, color: string): number {
  const s = `${kind}:${color}`;
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return (Math.abs(h) % 1000) / 1000;
}

function buildUniforms(params: StarVisualParams, seed: number): Record<string, THREE.IUniform> {
  return {
    uTime: { value: 0 },
    uResolution: { value: new THREE.Vector2(1, 1) },
    uCenterPx: { value: new THREE.Vector2(0, 0) },
    uRadiusPx: { value: 1 },
    uColorCore: { value: new THREE.Vector3(...params.colorCore) },
    uColorMid: { value: new THREE.Vector3(...params.colorMid) },
    uColorSpot: { value: new THREE.Vector3(...params.colorSpot) },
    uGranulationScale: { value: params.granulationScale },
    uGranulationContrast: { value: params.granulationContrast },
    uDomainWarpStrength: { value: params.domainWarpStrength },
    uLimbPower: { value: params.limbPower },
    uCoronaReach: { value: params.coronaReach },
    uFlareRate: { value: params.flareRate },
    uSunspotAmount: { value: params.sunspotAmount },
    uCmeRate: { value: params.cmeRate },
    uIsBlackHole: { value: params.isBlackHole ? 1 : 0 },
    uLensingStrength: { value: params.lensingStrength },
    uSeed: { value: seed },
  };
}

function applyParamsToUniforms(
  uniforms: Record<string, THREE.IUniform>,
  params: StarVisualParams,
  seed: number,
): void {
  (uniforms.uColorCore.value as THREE.Vector3).set(...params.colorCore);
  (uniforms.uColorMid.value as THREE.Vector3).set(...params.colorMid);
  (uniforms.uColorSpot.value as THREE.Vector3).set(...params.colorSpot);
  uniforms.uGranulationScale.value = params.granulationScale;
  uniforms.uGranulationContrast.value = params.granulationContrast;
  uniforms.uDomainWarpStrength.value = params.domainWarpStrength;
  uniforms.uLimbPower.value = params.limbPower;
  uniforms.uCoronaReach.value = params.coronaReach;
  uniforms.uFlareRate.value = params.flareRate;
  uniforms.uSunspotAmount.value = params.sunspotAmount;
  uniforms.uCmeRate.value = params.cmeRate;
  uniforms.uIsBlackHole.value = params.isBlackHole ? 1 : 0;
  uniforms.uLensingStrength.value = params.lensingStrength;
  uniforms.uSeed.value = seed;
}

export interface StarDiscProps {
  /** The ONE shared tableauFxHarness instance (WindshieldTableau's own
   *  `useTableauFx(containerRef)`, containerRef -> the `.scene.space` box).
   *  `null` before the harness mounts -- this component simply doesn't
   *  register until it's ready, matching `useTableauFx`'s own doc-comment
   *  usage example. */
  harness: TableauFxHarness | null;
  /** The same `StarAnchor` (xPct/yPct/sizeEm) windshieldTableauLayout.ts
   *  already computes for the DOM `.sun` button. */
  star: { xPct: number; yPct: number; sizeEm: number };
  /** celestial_service.py:110-171's 11 star kinds (`M_DWARF`..`BLACK_HOLE`). */
  kind: string;
  /** `system.star.color` -- the server's canonical per-kind hex. */
  color: string;
  /** Live px-per-1em (WindshieldTableau's own `bandBox.remPx`) so the
   *  shader's disc radius matches the `.sun` DOM button's real rendered
   *  footprint. Falls back to `DEFAULT_REM_PX` until the caller has one. */
  remPx?: number;
  /** Deterministic per-star phase offset for CME/flare timing (binary
   *  systems). Defaults to a hash of kind+color -- see `defaultSeed`. */
  seed?: number;
  className?: string;
}

/**
 * Renders ONE star as a roiling-plasma WebGL disc (WO-AAA-SOLAR-TABLEAU
 * phase 2). A single fullscreen shader quad — see `starShader.ts`'s own
 * header for the "2D sphere impostor" + single-pass corona technique.
 * Registers its one `<canvas>` with the shared `tableauFxHarness` (raw
 * `THREE.WebGLRenderer` bound directly to that canvas — NOT an R3F
 * `<Canvas>`, which would want to own its own rAF loop and fight the
 * harness's single shared clock). `pointer-events:none`, sized to the same
 * container box every other harness consumer overlays; layering it under
 * the existing DOM `.sun` button is Phase-3's job (WindshieldTableau.tsx),
 * not this component's — this file never touches that component.
 */
export default function StarDisc({
  harness,
  star,
  kind,
  color,
  remPx = DEFAULT_REM_PX,
  seed,
  className,
}: StarDiscProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const glRef = useRef<{ renderer: THREE.WebGLRenderer; material: THREE.ShaderMaterial } | null>(null);

  // Read live inside the draw closure without re-registering with the
  // harness on every prop change (position/size can update every render;
  // tearing down + rebuilding the GL context for that would be wasteful).
  const liveRef = useRef({ star, remPx });
  liveRef.current = { star, remPx };

  useEffect(() => {
    if (!harness || !canvasRef.current) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvasRef.current,
        alpha: true,
        antialias: false, // a fullscreen shader quad's edges are anti-aliased
                           // per-pixel (smoothstep in the shader), not by MSAA
        powerPreference: 'low-power',
        // Real on-screen presentation is UNAFFECTED either way (a browser's
        // implicit post-present clear only fires after this rAF tick's
        // render() call returns, never mid-callback) -- this exists purely
        // so a browser-proof (toDataURL()/readPixels() called from OUTSIDE
        // the render loop, e.g. Playwright, per this WO's own Proof
        // section) reads the actual last-drawn frame instead of racing the
        // browser's own buffer-clear-after-present cycle. Verified this is
        // a real, reproducible false-frozen trap: an external read via
        // requestAnimationFrame + a separate page.evaluate() call
        // consistently read back (0,0,0,0) with preserveDrawingBuffer's
        // default (false) despite confirmed-live rendering, and read the
        // correct pixel content with this set -- see the monk agent
        // notebook's star-disc-preserve-drawing-buffer entry.
        preserveDrawingBuffer: true,
      });
    } catch (err) {
      // A headless/software-disabled environment with no real WebGL context
      // (jsdom's canvas has none) throws synchronously from the
      // WebGLRenderer constructor itself -- degrade to "no sun disc" (the
      // `.sun` DOM button stays, just without its canvas fill) instead of
      // crashing the whole windshield tree over a decorative effect.
      // eslint-disable-next-line no-console
      console.warn('StarDisc: WebGL unavailable, skipping sun disc render:', err);
      return;
    }
    renderer.setClearColor(0x000000, 0);

    const params = starVisualParams(kind, color);
    const starSeed = seed ?? defaultSeed(kind, color);
    const geometry = new THREE.PlaneGeometry(2, 2);
    const material = new THREE.ShaderMaterial({
      vertexShader: STAR_VERTEX_SHADER,
      fragmentShader: STAR_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      // The vertex shader bypasses modelViewMatrix/projectionMatrix entirely
      // (`gl_Position = vec4(position.xy, 0, 1)`), so PlaneGeometry's
      // object-space winding order is never re-oriented by a camera
      // transform the way three.js's default FrontSide culling assumes --
      // without DoubleSide the quad silently back-face-culls and NOTHING
      // rasterizes (verified via a raw `gl.readPixels` scan: maxAlpha=0
      // across the whole framebuffer with zero GL errors -- a fully
      // culled draw call, not a shader/uniform bug). See
      // `.claude/agent-memory/monk/star-disc-fullscreen-quad-backface-
      // cull-blank-canvas.md` for the full diagnosis.
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending, // matches StarField.tsx's own glow convention
      uniforms: buildUniforms(params, starSeed),
    });
    const mesh = new THREE.Mesh(geometry, material);
    const scene = new THREE.Scene();
    scene.add(mesh);
    // The vertex shader bypasses the projection pipeline entirely, so any
    // camera satisfies three.js's render() signature -- never consulted.
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

    glRef.current = { renderer, material };

    // Time origin for the uTime uniform -- captured via the SAME clock
    // source the harness itself uses (Date.now()/1000), but measured HERE
    // at mount rather than from the first `t` the draw callback receives.
    // WHY THIS MATTERS: the harness feeds `t = Date.now()/1000` (epoch
    // seconds, ~1.75e9 right now) straight through. A GLSL `float uTime` is
    // 32-bit -- at ~1.75e9 magnitude a float32's ULP is ~128, so a
    // frame-to-frame delta of even a full second is BELOW that precision:
    // `uTime` silently rounds to the SAME float32 value every frame and the
    // shader goes static. Proven empirically -- byte-identical canvas over
    // 21s+ of confirmed-live rAF ticks (`.claude/agent-memory/monk/
    // aaa-solar-tableau-stardisc-render-frozen-despite-live-rAF-and-utime.md`).
    // Subtracting `t0` keeps `uTime` small (starts ~0, grows by real
    // elapsed seconds) so every frame's delta stays comfortably
    // float32-representable for the life of the tab. Capturing `t0` from
    // `Date.now()/1000` HERE (not from the harness's first `t`) instead of
    // lazily on first callback matters for one edge case: if reduced-motion
    // (which pins the harness's `t` at exactly 0) is active at mount and
    // later toggles off mid-session, a first-callback-captured `t0` of 0
    // would silently reintroduce the exact epoch-magnitude bug this fixes
    // the moment real epoch values start flowing. With a mount-time origin,
    // `uTime = t - t0` stays small/precise whenever the harness feeds a
    // real epoch time, and stays a fixed (if large-magnitude) CONSTANT
    // whenever `t` is pinned at 0 for reduced-motion -- a constant needs no
    // frame-to-frame precision, only to be the same value every call, which
    // it trivially is (no separate reduced-motion branch needed here).
    const t0 = Date.now() / 1000;

    const unregister = harness.register(
      canvasRef.current,
      (t, mapper, size) => {
        renderer.setPixelRatio(size.dpr);
        renderer.setSize(size.cssWidth, size.cssHeight, false);

        const { star: liveStar, remPx: liveRemPx } = liveRef.current;
        const center = mapper(liveStar.xPct, liveStar.yPct);
        const radiusPx = (liveStar.sizeEm / 2) * liveRemPx;
        const dpr = size.dpr;

        const u = material.uniforms;
        u.uTime.value = t - t0;
        (u.uResolution.value as THREE.Vector2).set(size.cssWidth * dpr, size.cssHeight * dpr);
        (u.uCenterPx.value as THREE.Vector2).set(center.x * dpr, center.y * dpr);
        u.uRadiusPx.value = Math.max(1, radiusPx * dpr);

        renderer.render(scene, camera);
      },
      { manageSize: false },
    );

    return () => {
      unregister();
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      glRef.current = null;
    };
    // Only rebuild the GL context on harness identity change -- kind/color/
    // seed changes are pushed into the already-mounted material's uniforms
    // below instead of tearing down and recreating the WebGLRenderer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [harness]);

  // Push kind/color/seed changes into the mounted material without GL
  // churn. A star's kind/color don't change in normal play, but this keeps
  // the component correct if a caller ever swaps systems on one mounted
  // instance instead of remounting it.
  useEffect(() => {
    const gl = glRef.current;
    if (!gl) return;
    const params = starVisualParams(kind, color);
    applyParamsToUniforms(gl.material.uniforms, params, seed ?? defaultSeed(kind, color));
    harness?.drawNow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, color, seed]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
      aria-hidden="true"
    />
  );
}
