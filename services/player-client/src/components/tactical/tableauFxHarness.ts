import { useEffect, useState, type RefObject } from 'react';

/**
 * tableauFxHarness — the ONE shared animation clock/loop the AAA sun (WebGL)
 * and planet (Canvas-2D) tableau renderers both consume (WO-AAA-SOLAR-
 * TABLEAU phase 1, see `audit/design-briefs/aaa-solar-tableau-2026-07-18.md`
 * § "Shared harness"). Two independent rAF loops would phase-drift — the
 * sun's implied light direction and the planets' terminator/rim need to
 * agree frame-to-frame, so BOTH canvases register a draw callback here and
 * get called with the SAME `t` each accepted frame.
 *
 * Framework-light by design: `createTableauFxHarness` is a plain factory
 * (no React) so it's independently testable and reusable outside a
 * component; `useTableauFx` is a thin React binding that creates/destroys
 * one harness instance per mounted container.
 *
 * Sizing contract: both canvases overlay the SAME container box the `.sun`/
 * `.pl` DOM buttons are positioned against (`left:X%; top:Y%`,
 * WindshieldTableau.tsx) — the mapper below converts a %-anchor into that
 * same box's CSS-pixel space, so a canvas-drawn disc lines up with its own
 * (invisible-fill, hit-test-only) DOM button. The harness does NOT set a
 * registered canvas's CSS box size (`canvas.style.width/height` stays the
 * consumer's own CSS, e.g. `position:absolute; inset:0; width:100%;
 * height:100%`) — it only manages the backing store (`canvas.width/height`,
 * device pixels) for `manageSize: true` registrations, mirroring
 * SolarSystemViewscreen's own `canvas.width = Math.floor(w * dpr)` convention
 * (SolarSystemViewscreen.tsx:8108-8111).
 */

/** Drift cap; matches SolarSystemViewscreen.tsx:7580's flight/docked cadence
 *  exactly (same reasoning: this is a slow ambient drift, not an
 *  interaction-driven animation, so 24fps is plenty and keeps main-thread
 *  cost low for two always-mounted canvases). */
export const BASE_FRAME_MS = 1000 / 24;

/** DPR cap for registered canvases — two layered high-cost canvases (a GLSL
 *  fragment shader + a multi-layer Canvas-2D composite) make an uncapped
 *  3x/4x HiDPI backing store expensive for no visible gain at this element
 *  size. Mirrors Galaxy3DRenderer.tsx:802's `Math.min(devicePixelRatio, 1.5)`
 *  R3F cap in spirit; 2 is this harness's default (tunable per-registration
 *  via `maxDpr` if a later phase's GL cost needs a tighter cap). */
export const DEFAULT_MAX_DPR = 2;

export interface PctPoint {
  xPct: number;
  yPct: number;
}

export interface PxPoint {
  x: number;
  y: number;
}

/** %-anchor -> px mapper, in the container's current CSS-pixel box (NOT
 *  device pixels — both a 2D `ctx` and a `THREE.WebGLRenderer` sized via
 *  `setSize(cssWidth, cssHeight, false)` operate in CSS-pixel coordinates
 *  and handle their own DPR scaling internally). */
export type TableauFxMapper = (xPct: number, yPct: number) => PxPoint;

/** Pure %-anchor -> px conversion, factored out of the harness so it's
 *  testable with zero DOM (mirrors windshieldTableauLayout.ts's pure-
 *  geometry-function convention). The harness's own `mapper` is a thin
 *  closure over this using the container's live cached size. */
export function pctToPx(xPct: number, yPct: number, cssWidth: number, cssHeight: number): PxPoint {
  return { x: (xPct / 100) * cssWidth, y: (yPct / 100) * cssHeight };
}

export interface TableauFxSize {
  /** The shared container's current CSS-pixel box — identical across every
   *  registration (all canvases overlay the same box). */
  cssWidth: number;
  cssHeight: number;
  /** This registration's own capped devicePixelRatio (see `maxDpr`). */
  dpr: number;
}

export type TableauFxDrawFn = (t: number, mapper: TableauFxMapper, size: TableauFxSize) => void;

export interface TableauFxRegisterOptions {
  /** Default true: the harness sets `canvas.width`/`canvas.height` (the
   *  backing store) directly on every resize — the right default for a
   *  Canvas-2D consumer.
   *  A WebGL consumer that owns a `THREE.WebGLRenderer` should pass `false`
   *  and size itself from the `size` argument each draw call
   *  (`renderer.setPixelRatio(size.dpr); renderer.setSize(size.cssWidth,
   *  size.cssHeight, false)`) — letting three.js's own `setSize` touch
   *  `canvas.width/height` avoids the harness and three.js racing to both
   *  mutate the same backing store. The harness still calls `draw`
   *  synchronously on registration and on every resize even when
   *  `manageSize` is false, so that first/resize call IS your resize hook —
   *  no separate `onResize` callback needed. */
  manageSize?: boolean;
  /** Overrides `DEFAULT_MAX_DPR` for this registration only. */
  maxDpr?: number;
}

export interface TableauFxHarness {
  /** Registers a canvas + its draw callback with the shared loop. Returns an
   *  unregister function (call on unmount) — mirrors this codebase's
   *  `subscribeXxx(): () => void` convention (deckNavBus.ts). Draws once,
   *  synchronously, immediately (so a newly-mounted canvas is never blank
   *  until the next tick/resize). */
  register(canvas: HTMLCanvasElement, draw: TableauFxDrawFn, options?: TableauFxRegisterOptions): () => void;
  /** Forces one synchronous draw pass of every registration at the current
   *  `t`, bypassing the frame-cap throttle — for a data change that isn't
   *  itself time-driven (mirrors SolarSystemViewscreen's `drawNowRef`). */
  drawNow(): void;
  /** Tears down every observer/listener and cancels the rAF loop. Call on
   *  unmount (`useTableauFx` does this for you). */
  destroy(): void;
}

interface Registration {
  canvas: HTMLCanvasElement;
  draw: TableauFxDrawFn;
  manageSize: boolean;
  maxDpr: number;
}

/** Live prefers-reduced-motion read, matching SolarSystemViewscreen.tsx:956's
 *  `window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches`
 *  optional-chained idiom (SSR/test-safe — no `window` guard needed thanks
 *  to the `?.`). */
function prefersReducedMotion(): boolean {
  return Boolean(
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
  );
}

/**
 * Creates one shared rAF loop + resize/visibility/reduced-motion machinery
 * for every canvas registered against `container`'s box. Framework-light —
 * safe to construct outside React (tests, non-component callers).
 */
export function createTableauFxHarness(container: HTMLElement): TableauFxHarness {
  const registrations = new Set<Registration>();

  let cssWidth = 1;
  let cssHeight = 1;
  let reducedMotion = prefersReducedMotion();
  let documentHidden = typeof document !== 'undefined' && document.hidden;
  // No IntersectionObserver in this env (or container not yet laid out) ->
  // default to "intersecting" so the loop isn't wrongly stuck paused.
  let intersecting = true;
  let rafId: number | undefined;
  let lastDrawMs = 0;

  const mapper: TableauFxMapper = (xPct, yPct) => pctToPx(xPct, yPct, cssWidth, cssHeight);

  const sizeFor = (reg: Registration): TableauFxSize => ({
    cssWidth,
    cssHeight,
    dpr: Math.min(typeof window !== 'undefined' ? (window.devicePixelRatio || 1) : 1, reg.maxDpr),
  });

  const drawOne = (reg: Registration, t: number) => {
    reg.draw(t, mapper, sizeFor(reg));
  };

  const drawAllNow = () => {
    const t = reducedMotion ? 0 : Date.now() / 1000;
    registrations.forEach((reg) => drawOne(reg, t));
  };

  const applyCanvasSize = (reg: Registration) => {
    if (!reg.manageSize) return;
    const dpr = sizeFor(reg).dpr;
    reg.canvas.width = Math.max(1, Math.floor(cssWidth * dpr));
    reg.canvas.height = Math.max(1, Math.floor(cssHeight * dpr));
  };

  const resizeAll = () => {
    const rect = container.getBoundingClientRect();
    cssWidth = Math.max(1, rect.width);
    cssHeight = Math.max(1, rect.height);
    registrations.forEach(applyCanvasSize);
    drawAllNow();
  };

  // ---- rAF loop: same "reschedule first, throttle second" shape as
  //      SolarSystemViewscreen.tsx:8135-8144 (the cap must sit below the
  //      display's vsync interval or it quantizes to every-other-vsync). ----
  const tick = (now: number) => {
    rafId = requestAnimationFrame(tick);
    if (now - lastDrawMs < BASE_FRAME_MS) return;
    lastDrawMs = now;
    drawAllNow();
  };

  const start = () => {
    if (rafId === undefined) rafId = requestAnimationFrame(tick);
  };
  const stop = () => {
    if (rafId !== undefined) {
      cancelAnimationFrame(rafId);
      rafId = undefined;
    }
  };

  /** Reduced-motion pins t=0 and paints a SINGLE static frame (no rAF loop)
   *  — matches SolarSystemViewscreen.tsx:2323/8127-8132's convention.
   *  Hidden/scrolled-offscreen simply pauses (perf only, no repaint owed). */
  const applyRunState = () => {
    const shouldRun = !reducedMotion && !documentHidden && intersecting;
    if (shouldRun) start();
    else {
      stop();
      if (reducedMotion) drawAllNow();
    }
  };

  // ---- initial size (synchronous, so register() has real numbers even
  //      before any observer fires) ----
  resizeAll();

  // ---- ResizeObserver — test/SSR-safe guard mirrors WindshieldTableau.tsx:639 ----
  let ro: ResizeObserver | undefined;
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(resizeAll);
    ro.observe(container);
  }

  // ---- IntersectionObserver — pause when the tableau scrolls offscreen.
  //      Optional perf enhancement; env without it just never pauses this way. ----
  let io: IntersectionObserver | undefined;
  if (typeof IntersectionObserver !== 'undefined') {
    io = new IntersectionObserver((entries) => {
      intersecting = entries.some((e) => e.isIntersecting);
      applyRunState();
    });
    io.observe(container);
  }

  // ---- document.hidden (tab backgrounded) ----
  let onVisibility: (() => void) | undefined;
  if (typeof document !== 'undefined') {
    onVisibility = () => {
      documentHidden = document.hidden;
      applyRunState();
    };
    document.addEventListener('visibilitychange', onVisibility);
  }

  // ---- live prefers-reduced-motion tracking ----
  let mql: MediaQueryList | undefined;
  let onReducedMotionChange: ((e: MediaQueryListEvent) => void) | undefined;
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    onReducedMotionChange = (e) => {
      reducedMotion = e.matches;
      applyRunState();
    };
    mql.addEventListener('change', onReducedMotionChange);
  }

  applyRunState();

  return {
    register(canvas, draw, options) {
      const reg: Registration = {
        canvas,
        draw,
        manageSize: options?.manageSize ?? true,
        maxDpr: options?.maxDpr ?? DEFAULT_MAX_DPR,
      };
      registrations.add(reg);
      applyCanvasSize(reg);
      drawOne(reg, reducedMotion ? 0 : Date.now() / 1000);
      return () => {
        registrations.delete(reg);
      };
    },
    drawNow: drawAllNow,
    destroy() {
      stop();
      ro?.disconnect();
      io?.disconnect();
      if (onVisibility && typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility);
      }
      if (mql && onReducedMotionChange) {
        mql.removeEventListener('change', onReducedMotionChange);
      }
      registrations.clear();
    },
  };
}

/**
 * React binding: creates one `TableauFxHarness` for `containerRef`'s element
 * on mount, destroys it on unmount. Returns `null` until the ref is attached
 * (matches the mount-order reality every other `containerRef`-driven effect
 * in this file family already handles — see WindshieldTableau.tsx:630-643).
 * A consumer (StarDisc.tsx / the planet-2D module) registers its OWN canvas
 * in its own effect once this returns non-null:
 *
 *   const harness = useTableauFx(containerRef);
 *   useEffect(() => {
 *     if (!harness || !canvasRef.current) return;
 *     return harness.register(canvasRef.current, draw);
 *   }, [harness]);
 */
export function useTableauFx(containerRef: RefObject<HTMLElement | null>): TableauFxHarness | null {
  const [harness, setHarness] = useState<TableauFxHarness | null>(null);

  // Empty deps, run once on mount -- containerRef's element is already
  // attached by the time an effect runs (it's set during the same render's
  // commit), matching WindshieldTableau.tsx:630's own containerRef effect.
  // ref identity is stable across renders so there is nothing reactive to
  // depend on here.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const h = createTableauFxHarness(el);
    setHarness(h);
    return () => {
      h.destroy();
      setHarness(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return harness;
}
