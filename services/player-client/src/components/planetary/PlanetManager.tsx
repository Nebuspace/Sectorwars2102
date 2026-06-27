import React, { useState, useEffect, useRef, useMemo } from 'react';
import { gameAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import type { Planet } from '../../types/planetary';
import GameLayout from '../layouts/GameLayout';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import EmptyState from '../common/EmptyState';
import './planet-manager.css';

/* COLONIAL REGISTRY console shell (Law 3) — module-level so the monitor
   frame keeps its identity across scanning/error/empty/registry branches
   (the scan spinner swaps INSIDE the frame, never unmounting it). */
const ColonialShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <GameLayout>
    <CockpitInstrument title="COLONIAL REGISTRY" accent="#7B2FFF" subtitle="PLANETARY OPERATIONS">
      {children}
    </CockpitInstrument>
  </GameLayout>
);

/**
 * Optional planet fields surfaced by newer gameserver payloads.
 * All reads are defensive — the roster renders gracefully when absent.
 */
interface PlanetExtras {
  population?: number;
  maxPopulation?: number;
  max_population?: number;
  habitability_score?: number;
  habitability?: {
    score?: number;
    effectiveMaxColonists?: number;
  };
  // Citadel level is used server-side for production/storage math but is not
  // (yet) part of the /planets/owned list payload; read it defensively in case
  // a future payload surfaces it under either casing.
  citadelLevel?: number;
  citadel_level?: number;
}

type PlanetWithExtras = Planet & PlanetExtras;

const getHabitabilityScore = (planet: PlanetWithExtras): number | null => {
  const score = planet.habitability?.score ?? planet.habitability_score;
  return typeof score === 'number' ? Math.max(0, Math.min(100, score)) : null;
};

const getPopulation = (planet: PlanetWithExtras): number =>
  planet.population ?? planet.colonists ?? 0;

const getMaxPopulation = (planet: PlanetWithExtras): number | null => {
  const explicit = planet.maxPopulation ?? planet.max_population;
  if (typeof explicit === 'number') return explicit;
  // Canon dual-ceiling fallback: max_population = habitability x 1000
  const hab = getHabitabilityScore(planet);
  return hab !== null ? hab * 1000 : null;
};

const getCitadelLevel = (planet: PlanetWithExtras): number | null => {
  const lvl = planet.citadelLevel ?? planet.citadel_level;
  return typeof lvl === 'number' ? lvl : null;
};

/** Efficiency = % of colonists actually assigned to production. `unused` is a
 *  raw colonist COUNT (colonists - allocated), so the share is
 *  (colonists - unused) / colonists, not `100 - unused`. (matches ProductionDashboard) */
const getEfficiency = (planet: Planet): number => {
  const colonists = planet.colonists ?? 0;
  if (colonists <= 0) return 0;
  const unused = planet.allocations?.unused ?? 0;
  return Math.max(0, Math.min(100, Math.round((100 * (colonists - unused)) / colonists)));
};

/** Parse the (string) sectorId into the numeric id moveToSector expects. */
const getNumericSectorId = (planet: Planet): number | null => {
  const raw = (planet as PlanetWithExtras & { sector_id?: string | number }).sector_id ?? planet.sectorId;
  const n = typeof raw === 'number' ? raw : parseInt(String(raw ?? ''), 10);
  return Number.isFinite(n) ? n : null;
};

const habitabilityBand = (score: number): 'low' | 'mid' | 'high' =>
  score < 40 ? 'low' : score < 70 ? 'mid' : 'high';

const efficiencyBand = (eff: number): 'low' | 'mid' | 'high' =>
  eff < 50 ? 'low' : eff < 75 ? 'mid' : 'high';

const formatNumber = (num: number): string => {
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return Math.round(num).toString();
};

type SortKey =
  | 'name'
  | 'sector'
  | 'citadel'
  | 'population'
  | 'fuel'
  | 'organics'
  | 'equipment'
  | 'habitability'
  | 'efficiency';

const COLUMNS: { key: SortKey; label: string; align?: 'left' | 'right' }[] = [
  { key: 'name', label: 'Colony', align: 'left' },
  { key: 'sector', label: 'Sector', align: 'left' },
  { key: 'citadel', label: 'Citadel', align: 'right' },
  { key: 'population', label: 'Population / Cap', align: 'right' },
  { key: 'fuel', label: '⛽ Fuel', align: 'right' },
  { key: 'organics', label: '🌿 Org', align: 'right' },
  { key: 'equipment', label: '⚙️ Equip', align: 'right' },
  { key: 'habitability', label: 'Hab', align: 'right' },
  { key: 'efficiency', label: 'Eff', align: 'right' },
];

const sortValue = (planet: PlanetWithExtras, key: SortKey): number | string => {
  switch (key) {
    case 'name':
      return planet.name.toLowerCase();
    case 'sector':
      return (planet.sectorName || '').toLowerCase();
    case 'citadel':
      return getCitadelLevel(planet) ?? -1;
    case 'population':
      return getPopulation(planet);
    case 'fuel':
      return planet.productionRates.fuel;
    case 'organics':
      return planet.productionRates.organics;
    case 'equipment':
      return planet.productionRates.equipment;
    case 'habitability':
      return getHabitabilityScore(planet) ?? -1;
    case 'efficiency':
      return getEfficiency(planet);
  }
};

export const PlanetManager: React.FC = () => {
  const { moveToSector } = useGame();
  // CRT-T1.5-9 §5.1: the colony refresh is SERVER-PUSHED, not locally guessed.
  // A genesis_progress (formation finished) or planetary_update frame bumps this
  // counter in WebSocketContext; the effect below re-fetches /planets/owned when
  // it changes — replacing the client-side formation setInterval that guessed
  // completion from a timer.
  const { planetaryEventSignal } = useWebSocket();
  const [planets, setPlanets] = useState<Planet[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Travel state: which colony's "Set course" is in flight (disables its button).
  const [coursePlanetId, setCoursePlanetId] = useState<string | null>(null);

  // Sort state for the roster table.
  const [sortBy, setSortBy] = useState<SortKey>('population');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // COLONY scan loading state: show a spinner while the registry loads, then
  // fall back to a retry affordance if nothing arrives within 10s. apiRequest
  // has no timeout, so a hung GET /planets/owned would otherwise spin forever.
  // Mirrors the NAV scan idiom in GameDashboard.
  const [scanTimedOut, setScanTimedOut] = useState(false);
  const [scanAttempt, setScanAttempt] = useState(0);

  // Display-only clock for the genesis terraforming countdown bar (ticks `nowMs`
  // once a second only while a planet is still forming, purely to animate the
  // remaining-time readout + progress fill). CRT-T1.5-9 §5.1: this no longer
  // GUESSES completion from the timer — the authoritative "formation finished"
  // signal arrives as a server-pushed genesis_progress frame (see the
  // planetaryEventSignal effect below). This clock just paints.
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const anyForming = planets.some((p) => (p as Planet).formationStatus === 'forming');
  useEffect(() => {
    if (!anyForming) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyForming]);

  // CRT-T1.5-9 §5.1: re-fetch the colony registry whenever the server pushes a
  // genesis_progress (formation complete) or planetary_update frame. Skip the
  // initial mount (signal 0); the mount effect below owns the first load.
  const planetaryEventRef = useRef(planetaryEventSignal);
  useEffect(() => {
    if (planetaryEventSignal === planetaryEventRef.current) return;
    planetaryEventRef.current = planetaryEventSignal;
    loadPlanets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planetaryEventSignal]);

  const fmtFormationLeft = (ms: number): string => {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  };

  useEffect(() => {
    loadPlanets();
  }, []);

  useEffect(() => {
    if (!loading) {
      setScanTimedOut(false);
      return;
    }
    const timer = setTimeout(() => setScanTimedOut(true), 10000);
    return () => clearTimeout(timer);
  }, [loading, scanAttempt]);

  // Request token: only the latest loadPlanets call may touch state, so a
  // late-settling hung fetch can't clobber the result of a successful retry.
  const loadRequestId = useRef(0);

  const loadPlanets = async () => {
    const requestId = ++loadRequestId.current;
    try {
      setError(null);
      const response = await gameAPI.planetary.getOwnedPlanets();
      if (requestId !== loadRequestId.current) return;
      setPlanets(response.planets || []);
    } catch (err) {
      if (requestId !== loadRequestId.current) return;
      setError('Failed to load planets');
      console.error('Error loading planets:', err);
    } finally {
      if (requestId === loadRequestId.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  };

  const handleRefresh = () => {
    setRefreshing(true);
    loadPlanets();
  };

  const handleRetryScan = () => {
    setScanTimedOut(false);
    setScanAttempt(attempt => attempt + 1); // re-arm the 10s timeout
    setLoading(true);
    loadPlanets();
  };

  // "Set course" — hand off to the existing travel flow. moveToSector takes a
  // numeric sector id; the in-sector land flow takes over once we arrive.
  const handleSetCourse = async (planet: Planet) => {
    const sectorId = getNumericSectorId(planet);
    if (sectorId === null) {
      setError(`No sector coordinates for ${planet.name}`);
      return;
    }
    setCoursePlanetId(planet.id);
    try {
      await moveToSector(sectorId);
    } catch (err) {
      console.error('Error setting course:', err);
      // moveToSector surfaces its own error into GameContext; nothing else to do.
    } finally {
      setCoursePlanetId(null);
    }
  };

  const handleSort = (key: SortKey) => {
    if (sortBy === key) {
      setSortOrder(order => (order === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(key);
      // Text columns default ascending; numeric columns default descending.
      setSortOrder(key === 'name' || key === 'sector' ? 'asc' : 'desc');
    }
  };

  const getPlanetTypeIcon = (type: string) => {
    const icons: Record<string, string> = {
      terran: '🌍',
      oceanic: '🌊',
      mountainous: '⛰️',
      desert: '🏜️',
      frozen: '❄️',
      ice: '❄️',
      glacial: '❄️',
      volcanic: '🌋',
      barren: '🪨'
    };
    return icons[(type || '').toLowerCase()] || '🪐';
  };

  // Empire totals header (reuses the ProductionDashboard metric model).
  const totals = useMemo(() => {
    return planets.reduce(
      (acc, p) => {
        acc.fuel += p.productionRates.fuel;
        acc.organics += p.productionRates.organics;
        acc.equipment += p.productionRates.equipment;
        acc.population += getPopulation(p as PlanetWithExtras);
        acc.efficiency += getEfficiency(p);
        return acc;
      },
      { fuel: 0, organics: 0, equipment: 0, population: 0, efficiency: 0 }
    );
  }, [planets]);

  const avgEfficiency = planets.length > 0 ? Math.round(totals.efficiency / planets.length) : 0;
  const siegedCount = planets.filter(p => p.underSiege).length;

  const sortedPlanets = useMemo(() => {
    const arr = [...planets] as PlanetWithExtras[];
    arr.sort((a, b) => {
      const av = sortValue(a, sortBy);
      const bv = sortValue(b, sortBy);
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortOrder === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortOrder === 'asc'
        ? (av as number) - (bv as number)
        : (bv as number) - (av as number);
    });
    return arr;
  }, [planets, sortBy, sortOrder]);

  if (loading) {
    return (
      <ColonialShell>
        <div className="planet-manager loading">
          <div className="planet-scan-state">
            {!scanTimedOut ? (
              <>
                <div className="planet-scan-spinner" aria-hidden="true"></div>
                <span className="planet-scan-text">SCANNING COLONY REGISTRY...</span>
              </>
            ) : (
              <>
                <span className="planet-scan-text warning">COLONY REGISTRY SCAN TIMED OUT — NO TELEMETRY</span>
                <button className="planet-scan-retry" onClick={handleRetryScan}>
                  ⟳ RETRY SCAN
                </button>
              </>
            )}
          </div>
        </div>
      </ColonialShell>
    );
  }

  if (error && planets.length === 0) {
    return (
      <ColonialShell>
        <div className="planet-manager error">
          <div className="planet-scan-state">
            <span className="planet-scan-text warning">{error}</span>
            <button className="planet-scan-retry" onClick={handleRetryScan}>
              ⟳ RETRY SCAN
            </button>
          </div>
        </div>
      </ColonialShell>
    );
  }

  if (planets.length === 0) {
    return (
      <ColonialShell>
        <div className="planet-manager empty">
          <EmptyState
            icon="🌌"
            title="No Planets Owned"
            message="You don't own any planets yet. Deploy a Genesis Device from your ship to create your first colony!"
          />
        </div>
      </ColonialShell>
    );
  }

  return (
    <ColonialShell>
      <div className="planet-manager roster">
        {/* Empire totals header */}
        <div className="roster-header">
          <div className="roster-title">
            <h3>Colony Roster ({planets.length})</h3>
            <span className="roster-subtitle">
              Read-only fleet glance · set course to land and manage in the cockpit
            </span>
          </div>
          <div className="roster-totals">
            <span className="roster-total" title="Total fuel production across all colonies">
              ⛽ {formatNumber(totals.fuel)}/day
            </span>
            <span className="roster-total" title="Total organics production across all colonies">
              🌿 {formatNumber(totals.organics)}/day
            </span>
            <span className="roster-total" title="Total equipment production across all colonies">
              ⚙️ {formatNumber(totals.equipment)}/day
            </span>
            <span className="roster-total" title="Total population across all colonies">
              🌐 {formatNumber(totals.population)}
            </span>
            <span className="roster-total" title="Average production efficiency">
              ⚡ {avgEfficiency}%
            </span>
            {siegedCount > 0 && (
              <span className="roster-total siege" title="Colonies currently under siege">
                ⚠ {siegedCount} under siege
              </span>
            )}
            <button
              onClick={handleRefresh}
              className="refresh-button"
              disabled={refreshing}
              title={refreshing ? 'Refresh in progress' : 'Refresh colony registry'}
              aria-label={refreshing ? 'Refresh in progress' : 'Refresh colony registry'}
            >
              {refreshing ? '🔄' : '🔃'}
            </button>
          </div>
        </div>

        {/* Roster table */}
        <div className="roster-table" role="table" aria-label="Owned colonies">
          <div className="roster-row roster-row-head" role="row">
            {COLUMNS.map(col => (
              <button
                key={col.key}
                role="columnheader"
                className={`roster-th ${col.align === 'right' ? 'right' : ''} ${sortBy === col.key ? 'active' : ''}`}
                onClick={() => handleSort(col.key)}
                aria-sort={sortBy === col.key ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
              >
                {col.label}
                {sortBy === col.key && <span className="sort-arrow">{sortOrder === 'asc' ? ' ↑' : ' ↓'}</span>}
              </button>
            ))}
            <span className="roster-th right action-col" role="columnheader">Action</span>
          </div>

          {sortedPlanets.map(planet => {
            const forming = (planet as Planet).formationStatus === 'forming';
            const startMs = planet.formationStartedAt ? new Date(planet.formationStartedAt).getTime() : null;
            const endMs = planet.formationCompleteAt ? new Date(planet.formationCompleteAt).getTime() : null;
            const remainMs = endMs ? Math.max(0, endMs - nowMs) : 0;
            const pct = (forming && startMs && endMs && endMs > startMs)
              ? Math.min(100, Math.max(0, ((nowMs - startMs) / (endMs - startMs)) * 100))
              : 0;

            const hab = getHabitabilityScore(planet);
            const citadel = getCitadelLevel(planet);
            const population = getPopulation(planet);
            const maxPopulation = getMaxPopulation(planet);
            const eff = getEfficiency(planet);
            const courseInFlight = coursePlanetId === planet.id;
            const canSetCourse = getNumericSectorId(planet) !== null && !forming;

            if (forming) {
              return (
                <div
                  key={planet.id}
                  className="roster-row forming"
                  role="row"
                >
                  <span className="roster-td colony-cell" role="cell">
                    <span className="planet-icon-badge">🌱</span>
                    <span className="colony-name">{planet.name}</span>
                  </span>
                  <span className="roster-td" role="cell">{planet.sectorName}</span>
                  <span className="roster-td forming-cell" role="cell" style={{ gridColumn: '3 / -1' }}>
                    <div className="forming-head">
                      <span className="forming-label">TERRAFORMING</span>
                      <span className="forming-remain">{endMs ? `${fmtFormationLeft(remainMs)} left` : 'forming…'}</span>
                    </div>
                    <div className="forming-bar"><div className="forming-bar-fill" style={{ width: `${pct}%` }} /></div>
                    <div className="forming-note">Invulnerable while forming · usable when complete</div>
                  </span>
                </div>
              );
            }

            return (
              <div
                key={planet.id}
                className={`roster-row ${planet.underSiege ? 'under-siege' : ''}`}
                role="row"
              >
                <span className="roster-td colony-cell" role="cell">
                  <span className="planet-icon-badge" data-planet-type={(planet.planetType || '').toLowerCase()}>
                    {getPlanetTypeIcon(planet.planetType)}
                  </span>
                  <span className="colony-name">{planet.name}</span>
                  {planet.underSiege && <span className="siege-indicator" title="Under siege">⚠</span>}
                </span>

                <span className="roster-td" role="cell">{planet.sectorName}</span>

                <span className="roster-td right" role="cell">
                  {citadel !== null ? `Lv${citadel}` : '—'}
                </span>

                <span className="roster-td right pop-cell" role="cell">
                  {formatNumber(population)}
                  {maxPopulation !== null && (
                    <span className="pop-cap"> / {formatNumber(maxPopulation)}</span>
                  )}
                </span>

                <span className="roster-td right" role="cell">{planet.productionRates.fuel}</span>
                <span className="roster-td right" role="cell">{planet.productionRates.organics}</span>
                <span className="roster-td right" role="cell">{planet.productionRates.equipment}</span>

                <span className="roster-td right" role="cell">
                  {hab !== null ? (
                    <span className={`hab-pill hab-${habitabilityBand(hab)}`}>{hab}</span>
                  ) : '—'}
                </span>

                <span className="roster-td right" role="cell">
                  <span className={`eff-pill eff-${efficiencyBand(eff)}`}>{eff}%</span>
                </span>

                <span className="roster-td right action-col" role="cell">
                  <button
                    className="set-course-button"
                    onClick={() => handleSetCourse(planet)}
                    disabled={!canSetCourse || courseInFlight}
                    title={
                      canSetCourse
                        ? `Set course for ${planet.sectorName} — land to manage in the cockpit`
                        : 'Sector coordinates unavailable'
                    }
                  >
                    {courseInFlight ? '⏳ Plotting…' : '🚀 Set course'}
                  </button>
                </span>
              </div>
            );
          })}
        </div>

        {error && (
          <div className="roster-error" role="alert">{error}</div>
        )}
      </div>
    </ColonialShell>
  );
};
