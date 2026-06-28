/**
 * Vista Engine — React integration component
 *
 * VistaCanvas mounts a VistaModel onto a <canvas> element and drives the
 * animation clock externally via the `clock` prop.  The engine handle
 * lifecycle is tied to the React component lifecycle:
 *
 *   mount      → createVistaEngine().generate(input) → engine.mount()
 *   clock prop → handle.setTime(clock)
 *   input prop → handle.update(partial) (hot-patch; full rebuild if model changes)
 *   resize     → ResizeObserver → handle.resize(w, h)
 *   unmount    → handle.dispose()
 *
 * No rAF loop lives here — the caller drives `clock` (e.g. via useAnimationFrame
 * or a lab scrubber).  setTime(0) is the frozen reduced-motion frame.
 */

import React, { useRef, useEffect, useCallback } from 'react';
import { createVistaEngine } from './index';
import type { VistaInput, VistaHandle } from './contract';

export interface VistaCanvasProps {
  /** The full VistaInput; a change triggers a full generate + remount. */
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

  // Mount / remount when the canvas ref is attached or the input changes.
  // A full remount regenerates the model (pipeline → canvas mount).
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

  // Initial mount + remount when input changes
  useEffect(() => {
    inputRef.current = input;
    mountEngine();
    return () => {
      if (handleRef.current) {
        handleRef.current.dispose();
        handleRef.current = null;
      }
    };
  }, [input, mountEngine]);

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
