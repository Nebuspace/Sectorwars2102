/**
 * Vista Engine — React integration component
 *
 * VistaCanvas mounts a VistaModel onto a <canvas> element and drives the
 * animation clock externally via the `clock` prop.  The engine handle
 * lifecycle is tied to the React component lifecycle:
 *
 *   mount          → createVistaEngine().generate(input) → engine.mount()
 *   clock prop     → handle.setTime(clock)
 *   input prop     → structural change (seed/type) → full generate + remount
 *                    non-structural change (sliders, view) → handle.update(partial)
 *   resize         → ResizeObserver → handle.resize(w, h)
 *   unmount        → handle.dispose()
 *
 * The structural/non-structural split eliminates canvas flash on every slider
 * drag.  A full remount (dispose → clearRect → resize → generate → mount)
 * is reserved for seed and planet.type changes only.  Slider and view-override
 * deltas route through handle.update(partial) — no canvas clear, no flash.
 *
 * No rAF loop lives here — the caller drives `clock` (e.g. via useAnimationFrame
 * or a lab scrubber).  setTime(0) is the frozen reduced-motion frame.
 */

import React, { useRef, useEffect, useCallback } from 'react';
import { createVistaEngine } from './index';
import type { VistaInput, VistaHandle } from './contract';

export interface VistaCanvasProps {
  /**
   * The full VistaInput.  Structural changes (seed or planet.type) trigger a
   * full generate + remount; all other changes hot-patch via handle.update().
   */
  input: VistaInput;
  /**
   * Wall-clock elapsed seconds since mount.  Pass 0 (or omit) for a frozen
   * reduced-motion frame.  Typically driven by requestAnimationFrame.
   */
  clock?: number;
  /** CSS class applied to the <canvas> element. */
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Renders a VistaModel onto a <canvas> element.
 *
 * The component manages the full engine lifecycle internally.  Consumers
 * only supply VistaInput + an optional animation clock.
 */
export function VistaCanvas({ input, clock = 0, className, style }: VistaCanvasProps): React.ReactElement {
  // Named export is canonical; default export provided for VistaLab compatibility.
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const handleRef = useRef<VistaHandle | null>(null);
  // Track the last input by reference for the update hot-path
  const inputRef = useRef<VistaInput>(input);

  // Sentinel values ensure the very first render always triggers a full mount.
  // After mount they track the last-mounted seed/type for structural change detection.
  const prevSeedRef = useRef<string>('');
  const prevTypeRef = useRef<string>('');

  // Full mount: dispose the existing handle, resize the canvas, regenerate the
  // model, and mount a fresh renderer.  Used for seed and planet.type changes.
  const mountEngine = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Clean up any prior mount
    if (handleRef.current) {
      handleRef.current.dispose();
      handleRef.current = null;
    }

    // Size the canvas to its CSS display size (DPR-aware)
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    const engine = createVistaEngine();
    const model = engine.generate(inputRef.current);
    const handle = engine.mount(model, { canvas, backend: 'canvas2d' });
    handle.setTime(clock);
    handleRef.current = handle;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Input change effect: routes structural changes to full remount and all other
  // changes (sliders, view overrides, toggles) to handle.update() — no dispose,
  // no canvas clear, no flash.
  //
  // No cleanup return here: the structural path disposes inline (above), and
  // the non-structural path intentionally preserves the canvas content.
  // Unmount cleanup is handled by the separate effect below.
  //
  // NOTE: handleRef.current === null is treated as structural to correctly
  // handle React StrictMode's double-invoke: the unmount cleanup nulls
  // handleRef after the first run, and the second run must remount even though
  // prevSeedRef/prevTypeRef already hold the seed (seed check alone would be
  // false → update path → no-op → blank canvas).
  useEffect(() => {
    inputRef.current = input;

    const isStructural =
      handleRef.current === null ||
      input.seed !== prevSeedRef.current ||
      input.planet.type !== prevTypeRef.current;

    prevSeedRef.current = input.seed;
    prevTypeRef.current = input.planet.type;

    if (isStructural) {
      mountEngine();
    } else if (handleRef.current) {
      // Hot-patch: let the backend re-render with the updated partial state.
      // No canvas clear → no flash even on rapid slider drag.
      handleRef.current.update(input);
    }
  }, [input, mountEngine]);

  // Unmount-only cleanup: dispose the live handle when the component leaves the tree.
  useEffect(() => {
    return () => {
      if (handleRef.current) {
        handleRef.current.dispose();
        handleRef.current = null;
      }
    };
  }, []);

  // Drive setTime on every clock tick without a full remount
  useEffect(() => {
    if (handleRef.current) {
      handleRef.current.setTime(clock);
    }
  }, [clock]);

  // ResizeObserver: resize the handle (and underlying canvas) when the
  // element's layout dimensions change.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = window.devicePixelRatio || 1;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry || !handleRef.current) return;
      const { width, height } = entry.contentRect;
      const w = Math.max(1, Math.round(width * dpr));
      const h = Math.max(1, Math.round(height * dpr));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      handleRef.current.resize(w, h);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ display: 'block', width: '100%', height: '100%', ...style }}
    />
  );
}

// Default export for VistaLab.tsx compatibility
export default VistaCanvas;
