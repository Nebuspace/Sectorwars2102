/**
 * Vista Parity Harness — DEV-only — Lane A2 / W7 milestone.
 *
 * Renders OLD (drawLandedScene) on the LEFT and the NEW vista engine
 * (VistaCanvas via adaptLandedSceneToVistaInput) on the RIGHT, side-by-side,
 * for a single planet type at a time.  Both sides are driven from ONE
 * representative LandedVistaSource per type so both canvases depict the SAME
 * planet — apples-to-apples across the two render paths.
 *
 * Route: /lab/vista-parity?type=TERRAN
 *
 * Supported ?type= values (12):
 *   TERRAN · OCEANIC · TROPICAL · MOUNTAINOUS · ARCTIC · VOLCANIC
 *   BARREN · DESERT · JUNGLE · ICE · ARTIFICIAL · GAS_GIANT
 * Unknown or absent ?type → falls back to TERRAN.
 *
 * Playwright testids:
 *   [data-testid="parity-old-container"]    — wraps the OLD canvas
 *   [data-testid="parity-engine-container"] — wraps the ENGINE canvas
 *   [data-testid="parity-ready"]            — hidden; appears when BOTH canvases
 *                                             report non-blank pixel content.
 *
 * Readiness protocol (mirrors VistaProof): a polling rAF loop samples both
 * canvases via getImageData until non-black pixels are confirmed in each,
 * then marks the combined gate.  Caps at MAX_SETTLE_FRAMES to avoid hanging.
 */

import { useRef, useEffect, useState } from 'react';
import type { LandedVistaSource } from '../../components/tactical/landedVistaAdapter';
import { adaptLandedSceneToVistaInput } from '../../components/tactical/landedVistaAdapter';
import {
  drawLandedScene,
  landedPalette,
} from '../../components/tactical/SolarSystemViewscreen';
import { VistaCanvas } from '../react';

// ---------------------------------------------------------------------------
// Representative per-type sources — ONE source drives BOTH sides.
//
// Fields mirror the FIXED_INPUTS in VistaProof.tsx so visual characteristics
// are comparable.  citadelLevel≥2 on a few types exercises the citadel skyline
// element.  moons≥1 on OCEANIC/JUNGLE/GAS_GIANT exercises sky-moon rendering.
// ---------------------------------------------------------------------------

const PARITY_SOURCES: Record<string, LandedVistaSource> = {
  TERRAN:      { seedKey: 'parity-TERRAN-001',      planetType: 'TERRAN',      habitability: 85, citadelLevel: 2, orbitAu: 1.0,  star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 0 },
  OCEANIC:     { seedKey: 'parity-OCEANIC-001',     planetType: 'OCEANIC',     habitability: 70, citadelLevel: 1, orbitAu: 1.05, star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 1 },
  TROPICAL:    { seedKey: 'parity-TROPICAL-001',    planetType: 'TROPICAL',    habitability: 74, citadelLevel: 1, orbitAu: 0.95, star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 0 },
  MOUNTAINOUS: { seedKey: 'parity-MOUNTAINOUS-001', planetType: 'MOUNTAINOUS', habitability: 52, citadelLevel: 2, orbitAu: 1.1,  star: { kind: 'K_ORANGE', color: '#ffd090' }, moons: 0 },
  ARCTIC:      { seedKey: 'parity-ARCTIC-001',      planetType: 'ARCTIC',      habitability: 22, citadelLevel: 1, orbitAu: 1.6,  star: { kind: 'K_ORANGE', color: '#ffd0a0' }, moons: 0 },
  VOLCANIC:    { seedKey: 'parity-VOLCANIC-001',    planetType: 'VOLCANIC',    habitability: 12, citadelLevel: 1, orbitAu: 0.3,  star: { kind: 'M_DWARF',  color: '#ff8060' }, moons: 0 },
  BARREN:      { seedKey: 'parity-BARREN-001',      planetType: 'BARREN',      habitability: 5,  citadelLevel: 0, orbitAu: 1.8,  star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 0 },
  DESERT:      { seedKey: 'parity-DESERT-001',      planetType: 'DESERT',      habitability: 22, citadelLevel: 1, orbitAu: 1.4,  star: { kind: 'A_BLUE',   color: '#e0eeff' }, moons: 0 },
  JUNGLE:      { seedKey: 'parity-JUNGLE-001',      planetType: 'JUNGLE',      habitability: 78, citadelLevel: 2, orbitAu: 0.9,  star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 1 },
  ICE:         { seedKey: 'parity-ICE-001',         planetType: 'ICE',         habitability: 18, citadelLevel: 1, orbitAu: 2.2,  star: { kind: 'K_ORANGE', color: '#ffcc80' }, moons: 0 },
  ARTIFICIAL:  { seedKey: 'parity-ARTIFICIAL-001',  planetType: 'ARTIFICIAL',  habitability: 62, citadelLevel: 2, orbitAu: 1.2,  star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 0 },
  GAS_GIANT:   { seedKey: 'parity-GAS_GIANT-001',   planetType: 'GAS_GIANT',   habitability: 0,  citadelLevel: 0, orbitAu: 5.2,  star: { kind: 'G_YELLOW', color: '#fff4d0' }, moons: 2 },
};

// Stable sectorId per type (feeds drawLandedScene geometry seed + cache key).
const SECTOR_IDS: Record<string, number> = {
  TERRAN: 101, OCEANIC: 102, TROPICAL: 103, MOUNTAINOUS: 104, ARCTIC: 105,
  VOLCANIC: 106, BARREN: 107, DESERT: 108, JUNGLE: 109, ICE: 110,
  ARTIFICIAL: 111, GAS_GIANT: 112,
};

// ---------------------------------------------------------------------------
// Build the LandedCtx-compatible env object the old renderer expects.
//
// LandedCtx is NOT exported from SolarSystemViewscreen.  We extract its type
// via Parameters<typeof drawLandedScene>[6] so TypeScript validates the shape
// without needing a separate import.
// ---------------------------------------------------------------------------

type LandedEnvParam = NonNullable<Parameters<typeof drawLandedScene>[6]>;

function buildLandedEnv(src: LandedVistaSource): LandedEnvParam {
  return {
    habitability:   src.habitability ?? 50,
    citadelLevel:   src.citadelLevel ?? 1,
    starKind:       src.star?.kind,
    starColor:      src.star?.color,
    orbitAu:        src.orbitAu,
    moons:          src.moons ?? 0,
    // landedPlanetId seeds per-world geometry (star positions, flora, etc.);
    // setting it to seedKey gives deterministic landforms per type.
    landedPlanetId: src.seedKey,
    // siblings and phaseDeg omitted → 0 sky siblings + default phase.
    // Keeps both sides comparable since the adapter also defaults siblingCount=0.
  };
}

// ---------------------------------------------------------------------------
// OldCanvas — synchronous drawLandedScene at t=0 (frozen FROZEN_DAY_PHASE=0.40)
// ---------------------------------------------------------------------------

interface OldCanvasProps {
  planetType: string;
  source:     LandedVistaSource;
  width:      number;
  height:     number;
}

function OldCanvas({ planetType, source, width, height }: OldCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    // canvas.width/height are set via HTML attributes (see JSX below); just draw.
    const pal = landedPalette(planetType);
    const env = buildLandedEnv(source);
    // t=0 → frozen at FROZEN_DAY_PHASE (0.40 = high-morning sun, always up).
    drawLandedScene(ctx, width, height, SECTOR_IDS[planetType] ?? 1, 0, pal, env);
  }, [planetType, source, width, height]);

  return <canvas ref={canvasRef} width={width} height={height} style={{ display: 'block' }} />;
}

// ---------------------------------------------------------------------------
// Max rAF poll iterations before marking ready anyway.
// ---------------------------------------------------------------------------
const MAX_SETTLE_FRAMES = 60;
const MIN_NONBLACK      = 20;

// ---------------------------------------------------------------------------
// Canvas dimensions: 710 px × 820 px per side, 20 px gap → 1440 px total.
// ---------------------------------------------------------------------------
const CANVAS_W = 710;
const CANVAS_H = 820;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function VistaParity() {
  const params    = new URLSearchParams(window.location.search);
  const typeParam = (params.get('type') ?? 'TERRAN').toUpperCase();
  const source    = PARITY_SOURCES[typeParam] ?? PARITY_SOURCES['TERRAN'];

  // Adapter produces the VistaInput for the engine side (null → unsupported type)
  const engineInput = adaptLandedSceneToVistaInput(source);

  // Combined readiness gate: both canvases non-blank
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let rafId: number;
    let attempts = 0;

    function canvasHasContent(testid: string): boolean {
      const container = document.querySelector(`[data-testid="${testid}"]`);
      if (!container) return false;
      const canvas = container.querySelector('canvas') as HTMLCanvasElement | null;
      if (!canvas || canvas.width < 2 || canvas.height < 2) return false;
      try {
        const ctx = canvas.getContext('2d');
        if (!ctx) return false;
        const sW = Math.min(200, canvas.width);
        const sH = Math.min(100, canvas.height);
        const { data } = ctx.getImageData(0, 0, sW, sH);
        let nonBlack = 0;
        for (let i = 0; i < data.length; i += 16) {
          if (data[i] > 5 || data[i + 1] > 5 || data[i + 2] > 5) nonBlack++;
        }
        return nonBlack >= MIN_NONBLACK;
      } catch {
        return false; // tainted canvas or cross-origin — skip
      }
    }

    function poll() {
      attempts++;
      const oldOk = canvasHasContent('parity-old-container');
      const engOk = canvasHasContent('parity-engine-container');

      if (oldOk && engOk) {
        setReady(true);
        return;
      }
      if (attempts < MAX_SETTLE_FRAMES) {
        rafId = requestAnimationFrame(poll);
      } else {
        // Cap reached — let the spec's content guard apply its verdict
        setReady(true);
      }
    }

    rafId = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafId);
  }, []);

  const label = source.planetType ?? typeParam;

  return (
    <div style={{ background: '#000', width: '100vw', minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>

      {/* Column headers */}
      <div style={{ display: 'flex', gap: 20, marginTop: 8, color: '#888', fontSize: 11, fontFamily: 'monospace', width: CANVAS_W * 2 + 20 }}>
        <div style={{ width: CANVAS_W, textAlign: 'center' }}>OLD — drawLandedScene</div>
        <div style={{ width: CANVAS_W, textAlign: 'center' }}>ENGINE — VistaCanvas</div>
      </div>

      {/* Side-by-side panels */}
      <div style={{ display: 'flex', gap: 20, marginTop: 4 }}>

        {/* LEFT: legacy renderer */}
        <div
          data-testid="parity-old-container"
          style={{ width: CANVAS_W, height: CANVAS_H, overflow: 'hidden', flexShrink: 0 }}
        >
          <OldCanvas planetType={typeParam} source={source} width={CANVAS_W} height={CANVAS_H} />
        </div>

        {/* RIGHT: vista engine */}
        <div
          data-testid="parity-engine-container"
          style={{ width: CANVAS_W, height: CANVAS_H, position: 'relative', flexShrink: 0 }}
        >
          {engineInput
            ? (
              <VistaCanvas
                input={engineInput}
                clock={0}
                style={{ width: '100%', height: '100%' }}
              />
            )
            : (
              <div style={{ color: '#f55', padding: 12, fontFamily: 'monospace', fontSize: 11 }}>
                adapter returned null for type: {label}
              </div>
            )
          }
        </div>
      </div>

      {/* Footer metadata */}
      <div style={{ color: '#555', fontSize: 11, fontFamily: 'monospace', marginTop: 8, textAlign: 'center' }}>
        Vista Parity &nbsp;|&nbsp;
        type: {label} &nbsp;|&nbsp;
        seed: {source.seedKey} &nbsp;|&nbsp;
        hab: {source.habitability} &nbsp;|&nbsp;
        citadel: {source.citadelLevel ?? 0} &nbsp;|&nbsp;
        moons: {source.moons ?? 0} &nbsp;|&nbsp;
        t=0 (frozen/day) &nbsp;|&nbsp;
        DEV-only
      </div>

      {/* Playwright combined readiness gate — hidden; appears only when both
          canvases report non-blank pixel content.  Spec waits on this element. */}
      {ready && <div data-testid="parity-ready" style={{ display: 'none' }} aria-hidden="true" />}
    </div>
  );
}
