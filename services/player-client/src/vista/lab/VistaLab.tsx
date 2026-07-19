/**
 * Vista Lab — dev-only art-iteration UI
 *
 * Accessible at /lab/vista in Vite dev + stage builds only.
 * Dead-code-eliminated from any production bundle via `import.meta.env.DEV`
 * guard at the route level (App.tsx).
 *
 * Controls (P0 — preserved):
 *   🎲 Randomize  — new random seed → rebuild input → repaint
 *   🔒 Lock       — freeze seed so sliders sweep one knob at a time
 *   Seed field    — editable; paste any string to replay that exact vista
 *   Habitability  — 0–100 slider, overrides input.planet.habitability live
 *   Atmosphere    — ON/OFF toggle, overrides input.planet.atmosphere.present live
 *   Planet type   — 12-tile picker (all PlanetType values from contract.ts)
 *   Inspector     — live generateVista() JSON + invariants.ok + Copy-Seed button
 *   Clock         — LIVE (RAF-driven) or SCRUBBED (slider 0–1 → deterministic phase)
 *
 * Controls (P1 — Environment accordion):
 *   Atmosphere Density — 0–1 slider → input.planet.atmosphere.density
 *   Temperature        — -1..+1 slider → input.planet.temperature
 *   Water Coverage     — 0–1 slider → input.planet.waterCoverage
 *   Native Life        — 0–1 slider → input.planet.nativeLife
 *   Star Kind          — select → input.celestial.star.kind (color auto-derived)
 *
 * Controls (Site accordion):
 *   Site toggle     — absent (P0 behavior) / enabled (expedition-roll fields)
 *   Shape           — GridShape select → input.site.shape
 *   Usable Slots    — 6–32 slider → input.site.usableSlots
 *   Citadel Cap     — 1–5 select → input.site.citadelCeiling
 *   Energy Source   — EnergySource select → input.site.energy.source
 *   Energy Tier     — 1–4 select → input.site.energy.tier
 *   Energy Magnitude — 0–1 slider → input.site.energy.magnitude
 *   Defensibility   — 0–1 slider → input.site.defensibility
 *   Deposits        — 6 preset kinds; richness slider 0–1 (0 = absent)
 *   Hazards         — 9 preset kinds covering all 9 distinct hazard visuals; severity 0–1 + named toggle
 *
 * Architecture:
 *   `randomVistaInput(seed, type)` builds the canonical base input.
 *   Lab overrides are layered on top via useMemo.  The base is re-built only
 *   when seed or type changes; P1 env sliders sync from baseInput on each
 *   randomize (when unlocked) so the visible scene matches the full roll.
 *   Existing habitability + atmospherePresent retain their independent behavior.
 *   A requestAnimationFrame clock drives the day-cycle animation via the `clock`
 *   prop passed to <VistaCanvas>.  The Clock control can override it with a
 *   deterministic scrubber value: phase (0–1) × DAY_CYCLE_SECONDS (360 s).
 *
 * Cross-lane imports (Lanes B + C — EXPECTED "cannot find module" until they land):
 *   generateVista  ← Lane B  src/vista/core/pipeline.ts
 *   VistaCanvas    ← Lane C  src/vista/react.tsx
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { PlanetType, VistaInput, StarKind, GridShape, EnergySource } from '../contract';
import { randomVistaInput } from '../core/validate';
// Lane C — will resolve once react.tsx lands; expected tsc gap until integrate
import VistaCanvas from '../react';
// Lane B — will resolve once pipeline.ts lands; expected tsc gap until integrate
import { generateVista } from '../core/pipeline';
// PERF-HARNESS sub-part (c) — reads sub-part (b)'s collector; expected tsc
// gap until that lane lands (see PerfOverlay.tsx's own doc comment).
import PerfOverlay from '../perf/PerfOverlay';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** All 12 planet types — typed against the PlanetType union in contract.ts. */
const ALL_PLANET_TYPES: PlanetType[] = [
  'TERRAN', 'DESERT', 'OCEANIC', 'ICE', 'VOLCANIC', 'GAS_GIANT',
  'BARREN', 'JUNGLE', 'ARCTIC', 'TROPICAL', 'MOUNTAINOUS', 'ARTIFICIAL',
];

const ALL_STAR_KINDS: StarKind[] = [
  'M_DWARF', 'K_ORANGE', 'G_YELLOW', 'F_WHITE', 'A_BLUE',
  'B_BLUE_GIANT', 'O_BLUE_SUPER', 'RED_GIANT', 'WHITE_DWARF', 'NEUTRON', 'BLACK_HOLE',
];

const ALL_GRID_SHAPES: GridShape[] = [
  'COMPACT', 'TERRACED', 'LINEAR', 'IRREGULAR', 'SPRAWLING', 'ENGINEERED',
];

const ALL_ENERGY_SOURCES: EnergySource[] = ['GEOTHERMAL', 'TIDAL', 'SOLAR', 'WIND'];

/**
 * Weather override options for the lab dropdown.
 * Values match view.weatherOverride in the contract (string | null).
 * null = let the pipeline/renderer pick based on atmosphere/type.
 * These cover all 7 particle kinds so every draw path is reachable in the lab.
 */
const WEATHER_OVERRIDE_OPTIONS: Array<{ value: string | null; label: string }> = [
  { value: null,    label: 'Auto' },
  { value: 'clear', label: 'Clear' },
  { value: 'rain',  label: 'Rain' },
  { value: 'storm', label: 'Storm' },
  { value: 'snow',  label: 'Snow' },
  { value: 'ash',   label: 'Ash' },
  { value: 'dust',  label: 'Dust' },
  { value: 'spore', label: 'Spore' },
  { value: 'ember', label: 'Ember' },
];

/**
 * Canonical hex colors per StarKind.
 * Mirrors the private STAR_COLORS constant in core/validate.ts (which uses
 * this mapping for randomVistaInput).  Duplicated here so the Environment
 * picker can display the swatch without importing a private symbol.
 */
const STAR_COLORS: Record<StarKind, string> = {
  M_DWARF:      '#ff6030',
  K_ORANGE:     '#ffaa60',
  G_YELLOW:     '#fff4d0',
  F_WHITE:      '#e8f0ff',
  A_BLUE:       '#c8d8ff',
  B_BLUE_GIANT: '#b0c8ff',
  O_BLUE_SUPER: '#a0b8ff',
  RED_GIANT:    '#ff3010',
  WHITE_DWARF:  '#e0f0ff',
  NEUTRON:      '#d0c0ff',
  BLACK_HOLE:   '#0a0010',
};

/** Preset deposit kinds for the Site panel (matches VistaModel depositMarker visuals). */
const DEPOSIT_PRESETS = ['ore', 'gas', 'thermal', 'hydrocarbon', 'crystal', 'biolumin'] as const;
type DepositKind = typeof DEPOSIT_PRESETS[number];

/**
 * Preset hazard kinds for the Site panel.
 * These are semantic hazard KIND strings, not visual names.  They match the keys
 * in each PlanetProfile's hazardVisuals map (profiles.ts).
 *
 * The set below covers every distinct hazard visual the engine can draw:
 *
 *   storm        → storm-cell        (TERRAN, OCEANIC, TROPICAL, GAS_GIANT)
 *   flood        → flood-zone        (TERRAN, OCEANIC, JUNGLE, TROPICAL)
 *   megafauna    → megafauna-marker  (TERRAN, JUNGLE, TROPICAL)
 *   radiation    → radiation-haze    (all types)
 *   seismic      → fault-line        (VOLCANIC, OCEANIC, MOUNTAINOUS, BARREN)
 *   magma-surge  → lava-flow         (VOLCANIC only)
 *   dust-storm   → dust-front        (DESERT only)
 *   micrometeor  → impact-scar       (DESERT, BARREN)
 *   blizzard     → snow-band         (ICE, ARCTIC, MOUNTAINOUS)
 *
 * To reach a type-specific visual, select the matching planet type first; then
 * enable the corresponding hazard kind.  Kinds absent from a type's hazardVisuals
 * map fall back to 'impact-scar' (engine default for unknown kinds).
 */
const HAZARD_PRESETS = [
  'storm', 'flood', 'megafauna', 'radiation',
  'seismic', 'magma-surge', 'dust-storm', 'micrometeor', 'blizzard',
] as const;
type HazardKind = typeof HAZARD_PRESETS[number];

/** Human-readable label for each hazard preset kind shown in the Site panel. */
const HAZARD_KIND_LABELS: Record<HazardKind, string> = {
  storm:          'Storm',
  flood:          'Flood',
  megafauna:      'Megafauna',
  radiation:      'Radiation',
  seismic:        'Seismic',
  'magma-surge':  'Magma Surge',
  'dust-storm':   'Dust Storm',
  micrometeor:    'Micrometeor',
  blizzard:       'Blizzard',
};

/**
 * Duration of one full day cycle in seconds — mirrors the engine constant
 * DAY_CYCLE_SECONDS exported from render/canvas2d/backend.ts.
 * The scrubber maps its 0–1 phase range to [0, DAY_CYCLE_SECONDS) so it
 * speaks the same elapsed-seconds domain that VistaCanvas.setTime() expects.
 * dayPhase inside the engine: 0=midnight, 0.25=sunrise, 0.5=noon, 0.75=sunset.
 */
const DAY_CYCLE_SECONDS = 360;

/** Generate a unique non-guessable seed string. */
function makeSeed(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// ---------------------------------------------------------------------------
// VistaLab
// ---------------------------------------------------------------------------

export default function VistaLab() {
  // ── P0 state (preserved from original) ──────────────────────────────────
  const [seed, setSeed] = useState<string>(() => makeSeed());
  const [locked, setLocked] = useState<boolean>(false);
  const [planetType, setPlanetType] = useState<PlanetType>('TERRAN');
  const [habitability, setHabitability] = useState<number>(50);
  const [atmospherePresent, setAtmospherePresent] = useState<boolean>(true);
  const [clock, setClock] = useState<number>(0);
  // Clock mode: LIVE uses the RAF-driven elapsed seconds; SCRUBBED overrides
  // with clockPhase × DAY_CYCLE_SECONDS so a given slider value always
  // produces the same frame regardless of when the component mounted.
  const [clockMode, setClockMode] = useState<'live' | 'scrubbed'>('live');
  const [clockPhase, setClockPhase] = useState<number>(0.5);    // 0.5 ≈ noon
  const [copied, setCopied] = useState<boolean>(false);

  // ── Environment overrides (P1) — sync from baseInput on randomize ────────
  const [envOpen, setEnvOpen] = useState<boolean>(false);
  const [temperature, setTemperature] = useState<number>(0);        // -1..+1
  const [waterCoverage, setWaterCoverage] = useState<number>(0.3);  // 0..1
  const [nativeLife, setNativeLife] = useState<number>(0.1);        // 0..1
  const [atmosphereDensity, setAtmosphereDensity] = useState<number>(0.7); // 0..1
  const [starKind, setStarKind] = useState<StarKind>('G_YELLOW');
  const [weatherOverride, setWeatherOverride] = useState<string | null>(null);

  // ── Site controls (absent = P0 behavior) ────────────────────────────────
  const [siteOpen, setSiteOpen] = useState<boolean>(false);
  const [siteEnabled, setSiteEnabled] = useState<boolean>(false);
  const [siteShape, setSiteShape] = useState<GridShape>('COMPACT');
  const [siteUsableSlots, setSiteUsableSlots] = useState<number>(16);
  const [siteCitadelCeiling, setSiteCitadelCeiling] = useState<1|2|3|4|5>(3);
  const [siteEnergySource, setSiteEnergySource] = useState<EnergySource>('SOLAR');
  const [siteEnergyTier, setSiteEnergyTier] = useState<1|2|3|4>(2);
  const [siteEnergyMagnitude, setSiteEnergyMagnitude] = useState<number>(0.5);
  const [siteDefensibility, setSiteDefensibility] = useState<number>(0.5);
  const [depositRichness, setDepositRichness] = useState<Partial<Record<DepositKind, number>>>({});
  const [hazardSeverity, setHazardSeverity] = useState<Partial<Record<HazardKind, number>>>({});
  const [hazardNamed, setHazardNamed] = useState<Partial<Record<HazardKind, boolean>>>({});

  // ── RAF clock ────────────────────────────────────────────────────────────
  const rafRef = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);

  // Ref for locked — lets the baseInput sync effect read current lock state
  // without being in its dependency array (avoids stale-closure without re-subscribing).
  const lockedRef = useRef(locked);
  lockedRef.current = locked;

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

  // ── Base input — rebuilt only when seed or type changes ──────────────────
  const baseInput = useMemo<VistaInput>(
    () => randomVistaInput(seed, planetType),
    [seed, planetType],
  );

  // ── Sync P1 environment sliders from baseInput on each new randomize ─────
  // Fires whenever baseInput changes (seed or type).  When locked, skips sync
  // so the user's manually-set values survive the type switch.
  // Existing habitability + atmospherePresent retain their independent behavior.
  useEffect(() => {
    if (!lockedRef.current) {
      setTemperature(baseInput.planet.temperature ?? 0);
      setWaterCoverage(baseInput.planet.waterCoverage ?? 0.3);
      setNativeLife(baseInput.planet.nativeLife ?? 0.1);
      setAtmosphereDensity(baseInput.planet.atmosphere.density);
      setStarKind(baseInput.celestial.star.kind);
    }
  }, [baseInput]);

  // ── Final input — base with all lab overrides layered on top ─────────────
  const vistaInput = useMemo<VistaInput>(() => {
    const site: VistaInput['site'] = siteEnabled
      ? {
          shape: siteShape,
          usableSlots: siteUsableSlots,
          citadelCeiling: siteCitadelCeiling,
          energy: {
            source: siteEnergySource,
            tier: siteEnergyTier,
            magnitude: siteEnergyMagnitude,
          },
          deposits: DEPOSIT_PRESETS
            .filter(k => (depositRichness[k] ?? 0) > 0)
            .map(k => ({ kind: k, richness: depositRichness[k] as number })),
          hazards: HAZARD_PRESETS
            .filter(k => (hazardSeverity[k] ?? 0) > 0)
            .map(k => ({
              kind: k,
              severity: hazardSeverity[k] as number,
              named: hazardNamed[k] ?? false,
            })),
          defensibility: siteDefensibility,
        }
      : undefined;

    return {
      ...baseInput,
      site,
      planet: {
        ...baseInput.planet,
        habitability,
        atmosphere: {
          ...baseInput.planet.atmosphere,
          present: atmospherePresent,
          density: atmosphereDensity,
        },
        temperature,
        waterCoverage,
        nativeLife,
      },
      celestial: {
        ...baseInput.celestial,
        star: {
          ...baseInput.celestial.star,
          kind: starKind,
          color: STAR_COLORS[starKind],
        },
      },
      // view is renderer-only — excluded from generate() determinism
      view: { weatherOverride },
    };
  }, [
    baseInput, habitability, atmospherePresent, atmosphereDensity,
    temperature, waterCoverage, nativeLife, starKind,
    siteEnabled, siteShape, siteUsableSlots, siteCitadelCeiling,
    siteEnergySource, siteEnergyTier, siteEnergyMagnitude,
    siteDefensibility, depositRichness, hazardSeverity, hazardNamed,
    weatherOverride,
  ]);

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

  // Environment handlers
  const handleTemperatureChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setTemperature(Number(e.target.value)),
    [],
  );
  const handleWaterCoverageChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setWaterCoverage(Number(e.target.value)),
    [],
  );
  const handleNativeLifeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setNativeLife(Number(e.target.value)),
    [],
  );
  const handleAtmosphereDensityChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setAtmosphereDensity(Number(e.target.value)),
    [],
  );
  const handleStarKindChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => setStarKind(e.target.value as StarKind),
    [],
  );
  const handleWeatherOverrideChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) =>
      setWeatherOverride(e.target.value === '' ? null : e.target.value),
    [],
  );

  // Site handlers
  const handleSiteEnergyTierChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) =>
      setSiteEnergyTier(Number(e.target.value) as 1|2|3|4),
    [],
  );
  const handleSiteCitadelCeilingChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) =>
      setSiteCitadelCeiling(Number(e.target.value) as 1|2|3|4|5),
    [],
  );
  const handleDepositRichnessChange = useCallback(
    (kind: DepositKind) => (e: React.ChangeEvent<HTMLInputElement>) =>
      setDepositRichness(prev => ({ ...prev, [kind]: Number(e.target.value) })),
    [],
  );
  const handleHazardSeverityChange = useCallback(
    (kind: HazardKind) => (e: React.ChangeEvent<HTMLInputElement>) =>
      setHazardSeverity(prev => ({ ...prev, [kind]: Number(e.target.value) })),
    [],
  );
  const handleHazardNamedToggle = useCallback(
    (kind: HazardKind) => () =>
      setHazardNamed(prev => ({ ...prev, [kind]: !(prev[kind] ?? false) })),
    [],
  );

  // Clock handlers
  const handleClockModeToggle = useCallback(
    (mode: 'live' | 'scrubbed') => () => setClockMode(mode),
    [],
  );
  const handleClockPhaseChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setClockPhase(Number(e.target.value)),
    [],
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={styles.root}>

      {/* ── Left rail: controls ─────────────────────────────────────────── */}
      <aside style={styles.rail}>
        <h2 style={styles.railTitle}>Vista Lab</h2>

        {/* Planet-type picker — all 12 PlanetType values in a 2-column grid */}
        <section style={styles.section}>
          <span style={styles.label}>Planet Type</span>
          <div style={styles.typePicker}>
            {ALL_PLANET_TYPES.map(t => (
              <button
                key={t}
                data-testid={`vista-lab-type-${t}`}
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
              data-testid="vista-lab-reseed"
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
            data-testid="vista-lab-habitability"
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

        {/* ── Clock mode + day-phase scrubber ─────────────────────────── */}
        <section style={styles.section}>
          <label style={styles.label}>Clock</label>
          <div style={styles.row}>
            <button
              onClick={handleClockModeToggle('live')}
              style={clockMode === 'live' ? styles.btnOn : styles.btnSecondary}
              title="Use RAF-driven elapsed time (animated)"
            >
              Live
            </button>
            <button
              onClick={handleClockModeToggle('scrubbed')}
              style={clockMode === 'scrubbed' ? styles.btnLocked : styles.btnSecondary}
              title="Override clock with slider — stable frame for screenshots"
            >
              Scrub
            </button>
          </div>
          {clockMode === 'scrubbed' && (
            <>
              <label style={{ ...styles.subLabel, marginTop: 6 }}>
                Day phase — {clockPhase.toFixed(3)}
                <span style={styles.clockHint}> (0=mid·0.25=rise·0.5=noon·0.75=set)</span>
              </label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.001}
                value={clockPhase}
                onChange={handleClockPhaseChange}
                style={styles.slider}
              />
            </>
          )}
        </section>

        {/* ── Environment accordion ────────────────────────────────────── */}
        <section style={styles.section}>
          <button
            onClick={() => setEnvOpen(v => !v)}
            style={styles.accordionHeader}
          >
            <span style={styles.accordionLabel}>Environment</span>
            <span style={styles.accordionChevron}>{envOpen ? '▲' : '▼'}</span>
          </button>
          {envOpen && (
            <div style={styles.accordionBody}>

              <label style={styles.label}>
                Atm. Density — {atmosphereDensity.toFixed(2)}
              </label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={atmosphereDensity}
                onChange={handleAtmosphereDensityChange}
                style={styles.slider}
              />

              <label style={styles.label}>
                Temperature — {temperature.toFixed(2)}
              </label>
              <input
                type="range"
                min={-1}
                max={1}
                step={0.01}
                value={temperature}
                onChange={handleTemperatureChange}
                style={styles.slider}
              />

              <label style={styles.label}>
                Water Coverage — {waterCoverage.toFixed(2)}
              </label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={waterCoverage}
                onChange={handleWaterCoverageChange}
                style={styles.slider}
              />

              <label style={styles.label}>
                Native Life — {nativeLife.toFixed(2)}
              </label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={nativeLife}
                onChange={handleNativeLifeChange}
                style={styles.slider}
              />

              <label style={styles.label}>Star Kind</label>
              <div style={styles.starRow}>
                <span
                  style={{
                    ...styles.starSwatch,
                    background: STAR_COLORS[starKind],
                  }}
                />
                <select
                  value={starKind}
                  onChange={handleStarKindChange}
                  style={styles.select}
                >
                  {ALL_STAR_KINDS.map(k => (
                    <option key={k} value={k}>{k}</option>
                  ))}
                </select>
              </div>

              <label style={styles.subLabel}>Weather</label>
              <select
                value={weatherOverride ?? ''}
                onChange={handleWeatherOverrideChange}
                style={styles.select}
              >
                {WEATHER_OVERRIDE_OPTIONS.map(({ value, label }) => (
                  <option key={value ?? '__null'} value={value ?? ''}>{label}</option>
                ))}
              </select>

            </div>
          )}
        </section>

        {/* ── Site accordion ───────────────────────────────────────────── */}
        <section style={styles.section}>
          <button
            onClick={() => setSiteOpen(v => !v)}
            style={styles.accordionHeader}
          >
            <span style={styles.accordionLabel}>
              Site {siteEnabled ? '●' : '○'}
            </span>
            <span style={styles.accordionChevron}>{siteOpen ? '▲' : '▼'}</span>
          </button>
          {siteOpen && (
            <div style={styles.accordionBody}>

              {/* Site enabled toggle */}
              <div style={styles.row}>
                <button
                  onClick={() => setSiteEnabled(v => !v)}
                  style={siteEnabled ? styles.btnOn : styles.btnOff}
                >
                  {siteEnabled ? 'ENABLED' : 'DISABLED'}
                </button>
              </div>

              {siteEnabled && (
                <>
                  <label style={styles.subLabel}>Shape</label>
                  <select
                    value={siteShape}
                    onChange={e => setSiteShape(e.target.value as GridShape)}
                    style={styles.select}
                  >
                    {ALL_GRID_SHAPES.map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>

                  <label style={styles.subLabel}>
                    Usable Slots — {siteUsableSlots}
                  </label>
                  <input
                    type="range"
                    min={6}
                    max={32}
                    value={siteUsableSlots}
                    onChange={e => setSiteUsableSlots(Number(e.target.value))}
                    style={styles.slider}
                  />

                  <label style={styles.subLabel}>Citadel Cap</label>
                  <select
                    value={siteCitadelCeiling}
                    onChange={handleSiteCitadelCeilingChange}
                    style={styles.select}
                  >
                    {([1, 2, 3, 4, 5] as const).map(n => (
                      <option key={n} value={n}>L{n}</option>
                    ))}
                  </select>

                  <label style={styles.subLabel}>Energy Source</label>
                  <select
                    value={siteEnergySource}
                    onChange={e => setSiteEnergySource(e.target.value as EnergySource)}
                    style={styles.select}
                  >
                    {ALL_ENERGY_SOURCES.map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>

                  <label style={styles.subLabel}>Energy Tier</label>
                  <select
                    value={siteEnergyTier}
                    onChange={handleSiteEnergyTierChange}
                    style={styles.select}
                  >
                    {([1, 2, 3, 4] as const).map(n => (
                      <option key={n} value={n}>Tier {n}</option>
                    ))}
                  </select>

                  <label style={styles.subLabel}>
                    Energy Magnitude — {siteEnergyMagnitude.toFixed(2)}
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={siteEnergyMagnitude}
                    onChange={e => setSiteEnergyMagnitude(Number(e.target.value))}
                    style={styles.slider}
                  />

                  <label style={styles.subLabel}>
                    Defensibility — {siteDefensibility.toFixed(2)}
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={siteDefensibility}
                    onChange={e => setSiteDefensibility(Number(e.target.value))}
                    style={styles.slider}
                  />

                  {/* Deposits: richness slider per preset kind; 0 = absent */}
                  <label style={styles.subLabel}>Deposits (0 = absent)</label>
                  {DEPOSIT_PRESETS.map(k => (
                    <div key={k} style={styles.presetRow}>
                      <span style={styles.presetName}>{k}</span>
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={depositRichness[k] ?? 0}
                        onChange={handleDepositRichnessChange(k)}
                        style={styles.presetSlider}
                      />
                      <span style={styles.presetVal}>
                        {Math.round((depositRichness[k] ?? 0) * 100)}%
                      </span>
                    </div>
                  ))}

                  {/* Hazards: severity slider per preset kind; N = named toggle */}
                  <label style={styles.subLabel}>Hazards (0 = absent)</label>
                  {HAZARD_PRESETS.map(k => (
                    <div key={k} style={styles.presetRow}>
                      <span style={styles.presetName}>{HAZARD_KIND_LABELS[k]}</span>
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={hazardSeverity[k] ?? 0}
                        onChange={handleHazardSeverityChange(k)}
                        style={styles.presetSlider}
                      />
                      <button
                        onClick={handleHazardNamedToggle(k)}
                        style={hazardNamed[k] ? styles.presetTagOn : styles.presetTag}
                        title="Named hazard — forces its visual into the sky"
                      >
                        N
                      </button>
                    </div>
                  ))}
                </>
              )}

            </div>
          )}
        </section>

      </aside>

      {/* ── Centre: 16:9 canvas ──────────────────────────────────────────── */}
      <main style={styles.canvasArea}>
        <div data-testid="vista-lab-canvas-box" style={styles.canvasBox}>
          <VistaCanvas
            input={vistaInput}
            clock={clockMode === 'live' ? clock : clockPhase * DAY_CYCLE_SECONDS}
          />
          <PerfOverlay />
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
//
// ⚠ Border rule: never mix `border` shorthand with `borderColor`/`borderWidth`
// in the same style object — React emits a warning.  BASE_BTN uses individual
// props so derived styles can safely override `borderColor` alone.
// ---------------------------------------------------------------------------

const BASE_BTN: React.CSSProperties = {
  padding: '6px 10px',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: '#2a3050',   // default; derived styles override only this
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
    width: 240,       // widened from 220 to accommodate 2-column type picker
    minWidth: 240,
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
  // Same as label but with top breathing room for accordion sub-sections
  subLabel: {
    display: 'block',
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.06em',
    color: '#6878a0',
    textTransform: 'uppercase',
    marginBottom: 4,
    marginTop: 8,
  },
  row: {
    display: 'flex',
    gap: 8,
  },

  // 2-column grid for all 12 planet types
  typePicker: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 4,
  },
  typeTile: {
    ...BASE_BTN,
    background: '#141826',
    color: '#8898c0',
    textAlign: 'left',
    fontSize: 10,
    padding: '5px 6px',
  },
  typeTileActive: {
    ...BASE_BTN,
    background: '#1e3060',
    color: '#88aaff',
    borderColor: '#3050a0',
    textAlign: 'left',
    fontSize: 10,
    padding: '5px 6px',
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
  // Dim annotation text inside a label (clock phase guide, etc.)
  clockHint: {
    fontSize: 9,
    color: '#404860',
    letterSpacing: 0,
    textTransform: 'none' as const,
    fontWeight: 400,
  },

  // ── Accordions ──────────────────────────────────────────────────────────
  accordionHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    width: '100%',
    background: '#0e1020',
    borderWidth: 1,
    borderStyle: 'solid',
    borderColor: '#1e2040',
    borderRadius: 4,
    padding: '5px 8px',
    cursor: 'pointer',
    fontFamily: 'monospace',
    boxSizing: 'border-box',
  },
  accordionLabel: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.06em',
    color: '#8898c0',
    textTransform: 'uppercase',
  },
  accordionChevron: {
    fontSize: 9,
    color: '#4858a0',
  },
  accordionBody: {
    marginTop: 8,
    paddingLeft: 2,
  },

  // ── Select ───────────────────────────────────────────────────────────────
  select: {
    width: '100%',
    boxSizing: 'border-box',
    background: '#0e1020',
    border: '1px solid #2a3050',
    borderRadius: 4,
    color: '#c8d0e0',
    fontFamily: 'monospace',
    fontSize: 11,
    padding: '4px 6px',
    marginBottom: 0,
  },

  // ── Star Kind row ────────────────────────────────────────────────────────
  starRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  starSwatch: {
    width: 14,
    height: 14,
    borderRadius: '50%',
    flexShrink: 0,
    borderWidth: 1,
    borderStyle: 'solid',
    borderColor: '#2a3050',
  },

  // ── Deposit / hazard preset rows ─────────────────────────────────────────
  presetRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    marginBottom: 5,
  },
  presetName: {
    fontSize: 9,
    color: '#4858a0',
    width: 66,
    flexShrink: 0,
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
    overflow: 'hidden',
    whiteSpace: 'nowrap',
  },
  presetSlider: {
    flex: 1,
    minWidth: 0,
  },
  presetVal: {
    fontSize: 9,
    color: '#6878a0',
    width: 28,
    textAlign: 'right',
    flexShrink: 0,
  },
  // Named-hazard toggle button — inactive state
  presetTag: {
    padding: '1px 5px',
    borderWidth: 1,
    borderStyle: 'solid',
    borderColor: '#2a3050',
    borderRadius: 3,
    background: '#0e1020',
    color: '#4858a0',
    fontSize: 9,
    cursor: 'pointer',
    fontFamily: 'monospace',
    flexShrink: 0,
  },
  // Named-hazard toggle button — active state
  presetTagOn: {
    padding: '1px 5px',
    borderWidth: 1,
    borderStyle: 'solid',
    borderColor: '#4a2840',
    borderRadius: 3,
    background: '#2a1830',
    color: '#c87888',
    fontSize: 9,
    cursor: 'pointer',
    fontFamily: 'monospace',
    flexShrink: 0,
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
