/**
 * Vista Engine — Perf Collector  (PERF-HARNESS sub-part (b))
 *
 * A single dev-only singleton the canvas2d renderer reports layer timings,
 * particle counts, and allocation churn into.  Consumers:
 *   - PerfOverlay.tsx (sub-part (c))    — live HUD, samples snapshot() every rAF.
 *   - benchmark.ts (this lane)          — headless 12-scene budget runner.
 *   - render/canvas2d/backend.ts (a)    — the actual frameStart/record/frameEnd
 *                                          call sites around each draw stage.
 *
 * Zero-cost when disabled: every method short-circuits on `enabled` BEFORE
 * touching any field, so leaving perfCollector wired into backend.ts's hot
 * path costs one boolean check per call in the default (disabled) case —
 * no allocation, no Date/performance call.  `enabled` is flipped true only
 * by the benchmark harness or by PerfOverlay's `?perf=1` / localStorage gate.
 *
 * Interface is PINNED (do not change field names/shapes without updating
 * every consumer above) — see WO-PERF-HARNESS.
 */

export interface PerfSnapshot {
  layers: Record<string, number>;
  particleCount: number;
  allocChurn: number;
  fps: number;
  frameMs: number;
}

class PerfCollector {
  enabled = false;                       // set true only under a dev flag / in the benchmark

  private _layers: Record<string, number> = {};
  private _particles = 0;
  private _alloc = 0;
  private _frameT0 = 0;
  private _fps = 0;
  private _lastFrameEnd = 0;
  private _frameMs = 0;

  /** Call once per frame, before any layer draws.  Resets the per-frame accumulators. */
  frameStart(): void {
    if (!this.enabled) return;
    this._layers = {};
    this._particles = 0;
    this._alloc = 0;
    this._frameT0 = performance.now();
  }

  /** Add `ms` to the running total for `layer` this frame (multiple calls per layer accumulate). */
  record(layer: string, ms: number): void {
    if (!this.enabled) return;
    this._layers[layer] = (this._layers[layer] ?? 0) + ms;
  }

  /** Set the live particle count for this frame (overwrite, not accumulate — it's a point-in-time count). */
  recordParticles(n: number): void {
    if (!this.enabled) return;
    this._particles = n;
  }

  /** Tally an allocation event (e.g. a new typed array / object created mid-frame). Accumulates. */
  recordAlloc(n = 1): void {
    if (!this.enabled) return;
    this._alloc += n;
  }

  /**
   * Call once per frame, after every layer draw.  Closes out frameMs and rolls
   * fps from the inter-frame delta (EMA-smoothed so a single slow/fast frame
   * doesn't whipsaw the reading — matches the "live HUD" use case in
   * PerfOverlay; the benchmark harness reads raw per-scene frameMs directly).
   */
  frameEnd(): void {
    if (!this.enabled) return;
    const now = performance.now();
    this._frameMs = now - this._frameT0;

    if (this._lastFrameEnd > 0) {
      const delta = now - this._lastFrameEnd;
      if (delta > 0) {
        const instantFps = 1000 / delta;
        this._fps = this._fps === 0 ? instantFps : this._fps * 0.9 + instantFps * 0.1;
      }
    }
    this._lastFrameEnd = now;
  }

  /** Snapshot the current frame's numbers.  Returns a fresh copy — safe to hold across frames. */
  snapshot(): PerfSnapshot {
    return {
      layers: { ...this._layers },
      particleCount: this._particles,
      allocChurn: this._alloc,
      fps: this._fps,
      frameMs: this._frameMs,
    };
  }
}

export const perfCollector = new PerfCollector();

// ---------------------------------------------------------------------------
// Dev-only window exposure — lets a Playwright driver read the FULL snapshot
// (page.evaluate(() => (window as any).__perfCollector.snapshot())) without
// reaching into module internals across the page's own bundle boundary.
// `import.meta.env.DEV` gates it out of prod builds; `typeof window` guards
// the vitest 'node' test environment, which has no window global — this file
// is imported directly by src/vista/perf/__tests__/collector.test.ts.
// ---------------------------------------------------------------------------
if (import.meta.env.DEV && typeof window !== 'undefined') {
  (window as any).__perfCollector = perfCollector;
}
