/**
 * PerfOverlay — dev-only performance HUD for the vista lab harness
 * (PERF-HARNESS sub-part (c)).
 *
 * Reads `perfCollector.snapshot()` (sub-part (b), `./collector`) each
 * animation frame and renders per-layer draw cost, total frame budget,
 * particle count, allocation churn, and fps.
 *
 * Absence from the production player build is TWO-LAYERED:
 *   1. Primary mechanism (already in place): VistaLab/VistaProof/VistaParity
 *      -- the only files that import this component -- are themselves
 *      gated in App.tsx behind `import.meta.env.DEV ? lazy(() => import(...))
 *      : null`. In a prod build that ternary collapses to `null` at compile
 *      time, so the dynamic import is statically unreachable and Rollup
 *      never includes VistaLab/VistaProof/VistaParity -- or anything they
 *      import, including this file and `./collector` -- in any output
 *      chunk. This is the same mechanism that already excludes the rest of
 *      the lab tree; nothing extra is needed for THIS file to inherit it.
 *   2. Defense in depth: `import.meta.env.DEV` is re-checked locally too,
 *      so this component is inert even if a future refactor accidentally
 *      imports it from a non-lab, always-loaded file.
 *
 * Runtime toggle (separate concern from prod-exclusion): even inside dev
 * mode, the overlay only mounts+samples when explicitly opted into via
 * `?perf=1` or `localStorage.vistaPerf === '1'` -- an art-iteration dev
 * working in the lab shouldn't have to look at a perf HUD they didn't ask
 * for. Read once at mount (a page-load-time decision, not live-reactive).
 *
 * LANDMINE (this component is a passive READER, must not itself become a
 * per-frame cost): `perfCollector.snapshot()` IS called every rAF tick (into
 * a ref, no re-render), but the React state driving the visible DOM only
 * updates on a slower interval (THROTTLE_MS) -- so sampling stays
 * frame-accurate for whatever internal averaging/fps math the collector
 * does, while this component's own render cost stays at ~6-7Hz, not 60Hz.
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { perfCollector, type PerfSnapshot } from './collector';
import { TARGET_FRAME_MS } from './scenes';

// DOM update cadence for the overlay's own re-render, independent of the
// per-frame sampling rate above -- 150ms ≈ 6.7Hz, well inside the "throttle
// to ~4-10Hz" landmine guidance and still reads as "live" to a human eye.
const THROTTLE_MS = 150;

function readPerfToggle(): boolean {
  if (!import.meta.env.DEV) return false;
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get('perf') === '1') return true;
    return window.localStorage.getItem('vistaPerf') === '1';
  } catch {
    // localStorage/URLSearchParams can throw in locked-down environments
    // (private browsing, sandboxed iframes) -- fail closed, not loud.
    return false;
  }
}

export default function PerfOverlay() {
  // Lazy initializer -- read once at mount, not on every render.
  const [enabled] = useState(readPerfToggle);
  const [snapshot, setSnapshot] = useState<PerfSnapshot | null>(null);
  const latestRef = useRef<PerfSnapshot | null>(null);

  useEffect(() => {
    if (!enabled) return;

    perfCollector.enabled = true;

    let rafId: number;
    function tick() {
      latestRef.current = perfCollector.snapshot();
      rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);

    const intervalId = window.setInterval(() => {
      setSnapshot(latestRef.current);
    }, THROTTLE_MS);

    return () => {
      perfCollector.enabled = false;
      cancelAnimationFrame(rafId);
      window.clearInterval(intervalId);
    };
  }, [enabled]);

  if (!import.meta.env.DEV || !enabled || !snapshot) return null;

  const overBudget = snapshot.frameMs > TARGET_FRAME_MS;
  const sortedLayers = Object.entries(snapshot.layers).sort((a, b) => b[1] - a[1]);

  return (
    <div style={styles.root} data-testid="perf-overlay">
      <div style={styles.headerRow}>
        <span style={styles.title}>PERF</span>
        <span style={overBudget ? styles.budgetOver : styles.budgetOk} data-testid="perf-budget">
          {snapshot.frameMs.toFixed(2)}ms / {TARGET_FRAME_MS}ms
        </span>
      </div>
      <div style={styles.statsRow}>
        <span data-testid="perf-fps">{snapshot.fps.toFixed(0)} fps</span>
        <span>{snapshot.particleCount} particles</span>
        <span>{snapshot.allocChurn} allocs</span>
      </div>
      <ul style={styles.layerList}>
        {sortedLayers.map(([name, ms]) => (
          <li key={name} style={styles.layerRow}>
            <span style={styles.layerName}>{name}</span>
            <span style={styles.layerMs}>{ms.toFixed(2)}ms</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles — matches VistaLab's own dev-tool visual vocabulary (monospace,
// dark CRT-console palette, badgeOk/badgeErr-style green/red semantics).
// ---------------------------------------------------------------------------

const styles: Record<string, CSSProperties> = {
  root: {
    position: 'absolute',
    top: 8,
    right: 8,
    zIndex: 50,
    width: 200,
    maxHeight: '80%',
    overflowY: 'auto',
    padding: '8px 10px',
    background: 'rgba(10, 10, 20, 0.85)',
    border: '1px solid #2a3050',
    borderRadius: 6,
    fontFamily: 'monospace',
    fontSize: 11,
    color: '#8898c0',
    pointerEvents: 'none',
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  title: {
    fontWeight: 700,
    letterSpacing: 1,
    color: '#6878a0',
  },
  budgetOk: {
    fontWeight: 600,
    color: '#60c890',
  },
  budgetOver: {
    fontWeight: 600,
    color: '#c86878',
  },
  statsRow: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 6,
    marginBottom: 6,
    paddingBottom: 6,
    borderBottom: '1px solid #1e2030',
    color: '#8898c0',
  },
  layerList: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  layerRow: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 8,
  },
  layerName: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  layerMs: {
    flexShrink: 0,
    color: '#a8b4d8',
  },
};
