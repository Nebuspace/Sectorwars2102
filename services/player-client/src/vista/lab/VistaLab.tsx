/**
 * Vista Lab — dev-only art-iteration UI
 *
 * Accessible at /lab/vista in Vite dev + stage builds only.
 * Dead-code-eliminated from any production bundle via `import.meta.env.DEV`
 * guard at the route level (App.tsx).
 *
 * Controls:
 *   🎲 Randomize  — new random seed → rebuild input → repaint
 *   🔒 Lock       — freeze seed so sliders sweep one knob at a time
 *   Seed field    — editable; paste any string to replay that exact vista
 *   Habitability  — 0–100 slider, overrides input.planet.habitability live
 *   Atmosphere    — ON/OFF toggle, overrides input.planet.atmosphere.present live
 *   Planet type   — tile picker (TERRAN / VOLCANIC at MVP)
 *   Inspector     — live generateVista() JSON + invariants.ok + Copy-Seed button
 *
 * Architecture:
 *   `randomVistaInput(seed, type)` builds the canonical base input.
 *   Slider overrides are layered on top via useMemo — the base is re-built only
 *   when seed or type changes; overrides update the derived vistaInput cheaply.
 *   A requestAnimationFrame clock drives the day-cycle animation via the `clock`
 *   prop passed to <VistaCanvas>.
 *
 * Cross-lane imports (Lanes B + C — EXPECTED "cannot find module" until they land):
 *   generateVista  ← Lane B  src/vista/core/pipeline.ts
 *   VistaCanvas    ← Lane C  src/vista/react.tsx
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { PlanetType, VistaInput } from '../contract';
import { randomVistaInput } from '../core/validate';
// Lane C — will resolve once react.tsx lands; expected tsc gap until integrate
import VistaCanvas from '../react';
// Lane B — will resolve once pipeline.ts lands; expected tsc gap until integrate
import { generateVista } from '../core/pipeline';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** MVP planet-type set; expand to all 12 in Phase 1. */
const LAB_PLANET_TYPES: PlanetType[] = ['TERRAN', 'VOLCANIC'];

/** Generate a unique non-guessable seed string. */
function makeSeed(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// ---------------------------------------------------------------------------
// VistaLab
// ---------------------------------------------------------------------------

export default function VistaLab() {
  const [seed, setSeed] = useState<string>(() => makeSeed());
  const [locked, setLocked] = useState<boolean>(false);
  const [planetType, setPlanetType] = useState<PlanetType>('TERRAN');
  const [habitability, setHabitability] = useState<number>(50);
  const [atmospherePresent, setAtmospherePresent] = useState<boolean>(true);
  const [clock, setClock] = useState<number>(0);
  const [copied, setCopied] = useState<boolean>(false);

  // requestAnimationFrame clock — drives the day-cycle; advances in wall seconds
  const rafRef = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    function tick(now: number) {
      if (startRef.current === null) startRef.current = now;
      setClock((now - startRef.current) / 1000);
      rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // Base VistaInput from seed + type; only rebuilt when those change
  const baseInput = useMemo<VistaInput>(
    () => randomVistaInput(seed, planetType),
    [seed, planetType],
  );

  // Final input — base with lab-controlled overrides applied on top
  const vistaInput = useMemo<VistaInput>(
    () => ({
      ...baseInput,
      planet: {
        ...baseInput.planet,
        habitability,
        atmosphere: { ...baseInput.planet.atmosphere, present: atmospherePresent },
      },
    }),
    [baseInput, habitability, atmospherePresent],
  );

  // Inspector model — pure derivation, memoised so it doesn't rerun on clock ticks
  const model = useMemo(() => generateVista(vistaInput), [vistaInput]);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleRandomize = useCallback(() => {
    if (!locked) setSeed(makeSeed());
  }, [locked]);

  const handleLock = useCallback(() => {
    setLocked(l => !l);
  }, []);

  const handleSeedChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setSeed(e.target.value),
    [],
  );

  const handleHabitabilityChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setHabitability(Number(e.target.value)),
    [],
  );

  const handleAtmosphereToggle = useCallback(() => {
    setAtmospherePresent(v => !v);
  }, []);

  const handleTypeSelect = useCallback((type: PlanetType) => {
    setPlanetType(type);
  }, []);

  const handleCopySeed = useCallback(() => {
    navigator.clipboard.writeText(seed).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [seed]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={styles.root}>

      {/* ── Left rail: controls ─────────────────────────────────────────── */}
      <aside style={styles.rail}>
        <h2 style={styles.railTitle}>Vista Lab</h2>

        {/* Planet-type picker */}
        <section style={styles.section}>
          <span style={styles.label}>Planet Type</span>
          <div style={styles.typePicker}>
            {LAB_PLANET_TYPES.map(t => (
              <button
                key={t}
                onClick={() => handleTypeSelect(t)}
                style={planetType === t ? styles.typeTileActive : styles.typeTile}
              >
                {t}
              </button>
            ))}
          </div>
        </section>

        {/* Randomize + Lock */}
        <section style={styles.section}>
          <div style={styles.row}>
            <button
              onClick={handleRandomize}
              disabled={locked}
              style={locked ? styles.btnDisabled : styles.btnPrimary}
            >
              🎲 Randomize
            </button>
            <button
              onClick={handleLock}
              style={locked ? styles.btnLocked : styles.btnSecondary}
              title={locked ? 'Unlock seed' : 'Lock seed — sweep sliders without reseeding'}
            >
              {locked ? '🔒' : '🔓'} {locked ? 'Locked' : 'Lock'}
            </button>
          </div>
        </section>

        {/* Seed field */}
        <section style={styles.section}>
          <label style={styles.label}>Seed</label>
          <input
            type="text"
            value={seed}
            onChange={handleSeedChange}
            style={styles.seedInput}
            spellCheck={false}
          />
        </section>

        {/* Habitability slider */}
        <section style={styles.section}>
          <label style={styles.label}>Habitability — {habitability}</label>
          <input
            type="range"
            min={0}
            max={100}
            value={habitability}
            onChange={handleHabitabilityChange}
            style={styles.slider}
          />
        </section>

        {/* Atmosphere toggle */}
        <section style={styles.section}>
          <label style={styles.label}>Atmosphere</label>
          <button
            onClick={handleAtmosphereToggle}
            style={atmospherePresent ? styles.btnOn : styles.btnOff}
          >
            {atmospherePresent ? 'ON' : 'OFF'}
          </button>
        </section>
      </aside>

      {/* ── Centre: 16:9 canvas ──────────────────────────────────────────── */}
      <main style={styles.canvasArea}>
        <div style={styles.canvasBox}>
          <VistaCanvas input={vistaInput} clock={clock} />
        </div>
      </main>

      {/* ── Right: inspector ─────────────────────────────────────────────── */}
      <aside style={styles.inspector}>
        <div style={styles.inspectorHeader}>
          <span style={styles.label}>Inspector</span>
          <span style={model.invariants.ok ? styles.badgeOk : styles.badgeErr}>
            {model.invariants.ok ? '✓ valid' : '✗ invalid'}
          </span>
          <button onClick={handleCopySeed} style={styles.copyBtn}>
            {copied ? 'Copied!' : 'Copy Seed'}
          </button>
        </div>

        {model.invariants.notes.length > 0 && (
          <ul style={styles.notesList}>
            {model.invariants.notes.map((note, i) => (
              <li key={i} style={styles.note}>{note}</li>
            ))}
          </ul>
        )}

        <pre style={styles.pre}>{JSON.stringify(model, null, 2)}</pre>
      </aside>

    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline styles — avoids a CSS-file dep on a dev-only component
// ---------------------------------------------------------------------------

const BASE_BTN: React.CSSProperties = {
  padding: '6px 10px',
  border: '1px solid #2a3050',
  borderRadius: 4,
  cursor: 'pointer',
  fontSize: 12,
  fontFamily: 'monospace',
};

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    height: '100vh',
    background: '#0a0a12',
    color: '#c8d0e0',
    fontFamily: 'monospace',
    overflow: 'hidden',
  },

  // ── Rail ────────────────────────────────────────────────────────────────
  rail: {
    width: 220,
    minWidth: 220,
    padding: '16px 12px',
    borderRight: '1px solid #1e2030',
    display: 'flex',
    flexDirection: 'column',
    overflowY: 'auto',
  },
  railTitle: {
    fontSize: 14,
    fontWeight: 700,
    letterSpacing: '0.08em',
    marginTop: 0,
    marginBottom: 16,
    color: '#88aaff',
    textTransform: 'uppercase',
  },
  section: {
    marginBottom: 18,
  },
  label: {
    display: 'block',
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.06em',
    color: '#6878a0',
    textTransform: 'uppercase',
    marginBottom: 6,
  },
  row: {
    display: 'flex',
    gap: 8,
  },
  typePicker: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  typeTile: {
    ...BASE_BTN,
    background: '#141826',
    color: '#8898c0',
    textAlign: 'left',
  },
  typeTileActive: {
    ...BASE_BTN,
    background: '#1e3060',
    color: '#88aaff',
    borderColor: '#3050a0',
    textAlign: 'left',
  },
  btnPrimary: {
    ...BASE_BTN,
    background: '#1e3060',
    color: '#88aaff',
  },
  btnSecondary: {
    ...BASE_BTN,
    background: '#141826',
    color: '#6878a0',
  },
  btnDisabled: {
    ...BASE_BTN,
    background: '#0e1020',
    color: '#383c50',
    cursor: 'not-allowed',
  },
  btnLocked: {
    ...BASE_BTN,
    background: '#2a1830',
    color: '#c87888',
    borderColor: '#4a2840',
  },
  btnOn: {
    ...BASE_BTN,
    background: '#1a3028',
    color: '#60c890',
    borderColor: '#2a5040',
  },
  btnOff: {
    ...BASE_BTN,
    background: '#201010',
    color: '#886878',
    borderColor: '#3a2020',
  },
  seedInput: {
    width: '100%',
    boxSizing: 'border-box',
    background: '#0e1020',
    border: '1px solid #2a3050',
    borderRadius: 4,
    color: '#c8d0e0',
    fontFamily: 'monospace',
    fontSize: 11,
    padding: '5px 8px',
  },
  slider: {
    width: '100%',
  },

  // ── Canvas area ──────────────────────────────────────────────────────────
  canvasArea: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
    overflow: 'hidden',
  },
  canvasBox: {
    aspectRatio: '16 / 9',
    maxWidth: '100%',
    maxHeight: '100%',
    width: '100%',
    position: 'relative',
    overflow: 'hidden',
    background: '#05050f',
  },

  // ── Inspector ────────────────────────────────────────────────────────────
  inspector: {
    width: 320,
    minWidth: 320,
    padding: '16px 12px',
    borderLeft: '1px solid #1e2030',
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
    overflowY: 'auto',
  },
  inspectorHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  badgeOk: {
    fontSize: 11,
    padding: '2px 6px',
    borderRadius: 10,
    fontWeight: 600,
    background: '#1a3028',
    color: '#60c890',
  },
  badgeErr: {
    fontSize: 11,
    padding: '2px 6px',
    borderRadius: 10,
    fontWeight: 600,
    background: '#301820',
    color: '#c86878',
  },
  copyBtn: {
    marginLeft: 'auto',
    padding: '3px 8px',
    background: '#141826',
    border: '1px solid #2a3050',
    borderRadius: 4,
    color: '#6878a0',
    fontFamily: 'monospace',
    fontSize: 11,
    cursor: 'pointer',
  },
  notesList: {
    margin: '0 0 8px',
    paddingLeft: 16,
    fontSize: 11,
    color: '#c87850',
  },
  note: {
    marginBottom: 2,
  },
  pre: {
    flex: 1,
    margin: 0,
    fontSize: 10,
    color: '#6878a0',
    lineHeight: 1.5,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
    overflowY: 'auto',
  },
};
