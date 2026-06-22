import React, { useState, useEffect, useRef } from 'react';
import { gameAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import type { Planet, ColonySpecialization } from '../../types/planetary';
import { ColonistAllocator } from './ColonistAllocator';
import { BuildingManager } from './BuildingManager';
import { DefenseConfiguration } from './DefenseConfiguration';
import { GenesisDeployment } from './GenesisDeployment';
import { ColonySpecialization as ColonySpecializationComponent } from './ColonySpecialization';
import { SiegeStatusMonitor } from './SiegeStatusMonitor';
import CitadelManager from './CitadelManager';
import GridManager from './GridManager';
import TerraformingPanel from './TerraformingPanel';
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
 * All reads are defensive — panels render gracefully when absent.
 */
interface PlanetExtras {
  morale?: number;
  population?: number;
  maxPopulation?: number;
  max_population?: number;
  isPopulationHub?: boolean;
  is_population_hub?: boolean;
  lastGrowthAt?: string;
  last_growth_at?: string;
  habitability_score?: number;
  habitability?: {
    score?: number;
    effectiveMaxColonists?: number;
    growthMultiplier?: number;
    moraleBonus?: number;
  };
  terraforming?: {
    active?: boolean;
    target?: number | string;
    progress?: number;
    startedAt?: string;
  } | null;
  terraforming_active?: boolean;
  terraforming_target?: number | string;
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

const isTerraformingActive = (planet: PlanetWithExtras): boolean =>
  Boolean(planet.terraforming?.active ?? planet.terraforming_active);

const getTerraformingTarget = (planet: PlanetWithExtras): number | string | null =>
  planet.terraforming?.target ?? planet.terraforming_target ?? null;

const isPopulationHub = (planet: PlanetWithExtras): boolean =>
  Boolean(planet.isPopulationHub ?? planet.is_population_hub);

/** Canon growth formula: colonists x 0.01 x habitability/100 per day. */
const getGrowthPerDay = (planet: PlanetWithExtras): number | null => {
  const hab = getHabitabilityScore(planet);
  if (hab === null) return null;
  return planet.colonists * 0.01 * (hab / 100);
};

/** Map raw planet type strings onto theme keys (TERRAN/OCEANIC/DESERT/ICE/VOLCANIC/BARREN...). */
const normalizePlanetType = (type: string): string => {
  const t = (type || '').toLowerCase();
  if (t === 'frozen' || t === 'glacial' || t === 'arctic') return 'ice';
  return t;
};

const habitabilityBand = (score: number): 'low' | 'mid' | 'high' =>
  score < 40 ? 'low' : score < 70 ? 'mid' : 'high';

interface HabitabilityRingProps {
  score: number | null;
  terraformingActive: boolean;
  terraformingTarget: number | string | null;
}

/** SVG radial gauge: red <40, amber <70, green >=70; pulses while terraforming. */
const HabitabilityRing: React.FC<HabitabilityRingProps> = ({
  score,
  terraformingActive,
  terraformingTarget,
}) => {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const filled = score !== null ? (score / 100) * circumference : 0;
  const band = score !== null ? habitabilityBand(score) : 'unknown';

  return (
    <div
      className={`habitability-ring hab-${band} ${terraformingActive ? 'terraforming' : ''}`}
      title={
        score !== null
          ? `Habitability ${score}/100${terraformingActive ? ` — terraforming in progress${terraformingTarget !== null ? ` (target: ${terraformingTarget})` : ''}` : ''}`
          : 'Habitability unknown'
      }
    >
      <svg viewBox="0 0 64 64" width="64" height="64" role="img" aria-label="Habitability gauge">
        <circle className="ring-track" cx="32" cy="32" r={radius} fill="none" strokeWidth="5" />
        <circle
          className="ring-value"
          cx="32"
          cy="32"
          r={radius}
          fill="none"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference - filled}`}
          transform="rotate(-90 32 32)"
        />
        <text className="ring-score" x="32" y="31" textAnchor="middle" dominantBaseline="central">
          {score !== null ? score : '—'}
        </text>
        <text className="ring-caption" x="32" y="44" textAnchor="middle">HAB</text>
      </svg>
      {terraformingActive && (
        <span className="terraforming-tag">
          ⬆ Terraforming{terraformingTarget !== null ? ` → ${terraformingTarget}` : ''}
        </span>
      )}
    </div>
  );
};

export const PlanetManager: React.FC = () => {
  const { playerState } = useGame();
  const [planets, setPlanets] = useState<Planet[]>([]);
  const [selectedPlanet, setSelectedPlanet] = useState<Planet | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showAllocator, setShowAllocator] = useState(false);
  const [showBuildingManager, setShowBuildingManager] = useState(false);
  const [showDefenseConfig, setShowDefenseConfig] = useState(false);
  const [showGenesisDeployment, setShowGenesisDeployment] = useState(false);
  const [showSpecialization, setShowSpecialization] = useState(false);
  const [showSiegeMonitor, setShowSiegeMonitor] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'citadel' | 'grid' | 'terraforming'>('overview');

  // COLONY scan loading state: show a spinner while the registry loads, then
  // fall back to a retry affordance if nothing arrives within 10s. apiRequest
  // has no timeout, so a hung GET /planets/owned would otherwise spin forever.
  // Mirrors the NAV scan idiom in GameDashboard.
  const [scanTimedOut, setScanTimedOut] = useState(false);
  const [scanAttempt, setScanAttempt] = useState(0);

  // Live clock for genesis terraforming countdowns (ticks only while a planet
  // is still forming; bumps a refresh once a timer elapses so the colony flips
  // to usable on its own).
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const anyForming = planets.some((p: any) => p?.formationStatus === 'forming');
  useEffect(() => {
    if (!anyForming) return;
    const id = window.setInterval(() => {
      const t = Date.now();
      setNowMs(t);
      const soonest = planets
        .filter((p: any) => p?.formationStatus === 'forming' && p?.formationCompleteAt)
        .map((p: any) => new Date(p.formationCompleteAt).getTime());
      if (soonest.length && t >= Math.min(...soonest)) {
        window.clearInterval(id);
        loadPlanets(); // a formation finished — re-fetch (server lazily completes it)
      }
    }, 1000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyForming, planets]);

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

      // Select first planet by default
      if (response.planets && response.planets.length > 0 && !selectedPlanet) {
        setSelectedPlanet(response.planets[0]);
      }
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

  const handlePlanetSelect = (planet: Planet) => {
    setSelectedPlanet(planet);
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

  const handlePlanetUpdate = (updatedPlanet: Planet) => {
    setPlanets(prevPlanets => 
      prevPlanets.map(p => p.id === updatedPlanet.id ? updatedPlanet : p)
    );
    setSelectedPlanet(updatedPlanet);
  };

  const getSpecializationIcon = (spec?: ColonySpecialization) => {
    const icons = {
      agricultural: '🌾',
      industrial: '🏭',
      military: '⚔️',
      research: '🔬',
      balanced: '⚖️'
    };
    return spec ? icons[spec] : '🌍';
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

  if (error) {
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
          message="You don't own any planets yet. Deploy a Genesis Device to create your first colony!"
          action={{
            label: '🌌 Deploy Genesis Device',
            onClick: () => setShowGenesisDeployment(true)
          }}
        />

        {showGenesisDeployment && (
          <div className="modal-overlay" onClick={() => setShowGenesisDeployment(false)}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
              <GenesisDeployment
                onSuccess={() => {
                  setShowGenesisDeployment(false);
                  loadPlanets();
                }}
                onClose={() => setShowGenesisDeployment(false)}
              />
            </div>
          </div>
        )}
      </div>
      </ColonialShell>
    );
  }

  return (
    <ColonialShell>
    <div className="planet-manager">
      {/* Planet List Sidebar */}
      <div className="planet-list">
        <div className="planet-list-header">
          <h3>Your Colonies ({planets.length})</h3>
          <div className="header-actions">
            <button
              onClick={() => setShowGenesisDeployment(true)}
              className="genesis-mini-button"
              title="Deploy a Genesis Device to seed a new colony in an empty sector"
              aria-label="Deploy Genesis Device"
            >
              🌌 Deploy Genesis
            </button>
            <button
              onClick={handleRefresh}
              className="refresh-button"
              disabled={refreshing}
              title={refreshing ? 'Refresh in progress' : 'Refresh planet data'}
              aria-label={refreshing ? 'Refresh in progress' : 'Refresh planet data'}
            >
              {refreshing ? '🔄' : '🔃'}
            </button>
          </div>
        </div>
        
        <div className="planet-items">
          {planets.map(planet => {
            const forming = (planet as any).formationStatus === 'forming';
            const startMs = (planet as any).formationStartedAt ? new Date((planet as any).formationStartedAt).getTime() : null;
            const endMs = (planet as any).formationCompleteAt ? new Date((planet as any).formationCompleteAt).getTime() : null;
            const remainMs = endMs ? Math.max(0, endMs - nowMs) : 0;
            const pct = (forming && startMs && endMs && endMs > startMs)
              ? Math.min(100, Math.max(0, ((nowMs - startMs) / (endMs - startMs)) * 100))
              : 0;
            return (
            <div
              key={planet.id}
              className={`planet-item ${selectedPlanet?.id === planet.id ? 'selected' : ''} ${planet.underSiege ? 'under-siege' : ''} ${forming ? 'forming' : ''}`}
              data-planet-type={normalizePlanetType(planet.planetType)}
              onClick={() => handlePlanetSelect(planet)}
            >
              <div className="planet-item-header">
                <span className="planet-icon planet-icon-badge">
                  {forming ? '🌱' : getPlanetTypeIcon(planet.planetType)}
                </span>
                <span className="planet-name">{planet.name}</span>
                {forming && <span className="forming-indicator" title="Genesis terraforming in progress">🌱</span>}
                {planet.underSiege && <span className="siege-indicator">🚨</span>}
              </div>

              {forming ? (
                <div className="planet-forming">
                  <div className="forming-head">
                    <span className="forming-label">TERRAFORMING</span>
                    <span className="forming-remain">{endMs ? `${fmtFormationLeft(remainMs)} left` : 'forming…'}</span>
                  </div>
                  <div className="forming-bar"><div className="forming-bar-fill" style={{ width: `${pct}%` }} /></div>
                  <div className="info-row">
                    <span className="label">Sector:</span>
                    <span className="value">{planet.sectorName}</span>
                  </div>
                  <div className="forming-note">Invulnerable while forming · usable when complete</div>
                </div>
              ) : (
                <>
                  <div className="planet-item-info">
                    <div className="info-row">
                      <span className="label">Sector:</span>
                      <span className="value">{planet.sectorName}</span>
                    </div>
                    <div className="info-row">
                      <span className="label">Colonists:</span>
                      <span className="value">
                        {planet.colonists.toLocaleString()} / {planet.maxColonists.toLocaleString()}
                      </span>
                    </div>
                    <div className="info-row">
                      <span className="label">Specialization:</span>
                      <span className="value">
                        {getSpecializationIcon(planet.specialization)} {planet.specialization || 'None'}
                      </span>
                    </div>
                  </div>

                  <div className="planet-item-production">
                    <div className="production-mini">
                      <span title="Fuel">⛽ {planet.productionRates.fuel}</span>
                      <span title="Organics">🌿 {planet.productionRates.organics}</span>
                      <span title="Equipment">⚙️ {planet.productionRates.equipment}</span>
                    </div>
                  </div>
                </>
              )}
            </div>
            );
          })}
        </div>
      </div>

      {/* Planet Details */}
      {selectedPlanet && (
        <div className="planet-details">
          <div
            className="planet-header"
            data-planet-type={normalizePlanetType(selectedPlanet.planetType)}
          >
            <div className="planet-header-title">
              <span
                className="planet-icon-badge header-badge"
                data-planet-type={normalizePlanetType(selectedPlanet.planetType)}
              >
                {getPlanetTypeIcon(selectedPlanet.planetType)}
              </span>
              <h2>{selectedPlanet.name}</h2>
              {isPopulationHub(selectedPlanet as PlanetWithExtras) && (
                <span
                  className="hub-badge"
                  title="Population hub — this colony anchors regional growth"
                >
                  ⭐ HUB
                </span>
              )}
            </div>
            {selectedPlanet.underSiege && (
              <div className="siege-warning">
                <span className="siege-icon">🚨</span>
                <span>PLANET UNDER SIEGE!</span>
                <button
                  className="siege-status-button"
                  onClick={() => setShowSiegeMonitor(true)}
                >
                  View Status
                </button>
              </div>
            )}
            <HabitabilityRing
              score={getHabitabilityScore(selectedPlanet as PlanetWithExtras)}
              terraformingActive={isTerraformingActive(selectedPlanet as PlanetWithExtras)}
              terraformingTarget={getTerraformingTarget(selectedPlanet as PlanetWithExtras)}
            />
          </div>

          <div className="planet-tabs" role="tablist" aria-label="Planet management tabs">
            <button
              role="tab"
              aria-selected={activeTab === 'overview'}
              className={`planet-tab ${activeTab === 'overview' ? 'active' : ''}`}
              onClick={() => setActiveTab('overview')}
            >
              🌐 Overview
            </button>
            <button
              role="tab"
              aria-selected={activeTab === 'citadel'}
              className={`planet-tab ${activeTab === 'citadel' ? 'active' : ''}`}
              onClick={() => setActiveTab('citadel')}
            >
              🏰 Citadel
            </button>
            <button
              role="tab"
              aria-selected={activeTab === 'grid'}
              className={`planet-tab ${activeTab === 'grid' ? 'active' : ''}`}
              onClick={() => setActiveTab('grid')}
            >
              🏗️ Grid
            </button>
            <button
              role="tab"
              aria-selected={activeTab === 'terraforming'}
              className={`planet-tab ${activeTab === 'terraforming' ? 'active' : ''}`}
              onClick={() => setActiveTab('terraforming')}
            >
              🌱 Terraforming
            </button>
          </div>

          {activeTab === 'citadel' && (
            <div className="planet-overview citadel-tab-content">
              <CitadelManager
                planetId={selectedPlanet.id}
                playerCredits={playerState?.credits ?? 0}
                stationedDrones={selectedPlanet.defenses?.drones}
                onUpdate={loadPlanets}
              />
            </div>
          )}

          {activeTab === 'grid' && (
            <div className="planet-overview citadel-tab-content">
              <GridManager
                planetId={selectedPlanet.id}
                playerCredits={playerState?.credits ?? 0}
                onUpdate={loadPlanets}
              />
            </div>
          )}

          {activeTab === 'terraforming' && (
            <div className="planet-overview citadel-tab-content">
              <TerraformingPanel
                planetId={selectedPlanet.id}
                playerCredits={playerState?.credits ?? 0}
                habitabilityScore={getHabitabilityScore(selectedPlanet as PlanetWithExtras)}
                onUpdate={loadPlanets}
              />
            </div>
          )}

          {activeTab === 'overview' && (
          <div className="planet-overview">
            {(() => {
              const extended = selectedPlanet as PlanetWithExtras;
              const habScore = getHabitabilityScore(extended);
              const population = getPopulation(extended);
              const maxPopulation = getMaxPopulation(extended);
              const growth = getGrowthPerDay(extended);
              const workforcePct = selectedPlanet.maxColonists > 0
                ? Math.min(100, (selectedPlanet.colonists / selectedPlanet.maxColonists) * 100)
                : 0;
              const populationPct = maxPopulation && maxPopulation > 0
                ? Math.min(100, (population / maxPopulation) * 100)
                : 0;
              const workforceWithinPopPct = maxPopulation && maxPopulation > 0
                ? Math.min(100, (selectedPlanet.colonists / maxPopulation) * 100)
                : 0;
              return (
                <div className="overview-section population-panel">
                  <h3>Population</h3>
                  <div className="dual-ceiling">
                    <div className="ceiling-bar-group">
                      <div className="ceiling-bar-header">
                        <span
                          className="ceiling-label workforce"
                          title="Working colonists, capped by citadel level"
                        >
                          👥 Workforce
                        </span>
                        <span className="ceiling-numbers">
                          {selectedPlanet.colonists.toLocaleString()} / {selectedPlanet.maxColonists.toLocaleString()}
                        </span>
                      </div>
                      <div className="ceiling-bar">
                        <div
                          className="ceiling-fill workforce"
                          style={{ width: `${workforcePct}%` }}
                        />
                      </div>
                    </div>
                    {maxPopulation !== null ? (
                      <div className="ceiling-bar-group">
                        <div className="ceiling-bar-header">
                          <span
                            className="ceiling-label population"
                            title="Total inhabitants, capped by habitability (habitability × 1,000)"
                          >
                            🌐 Population
                          </span>
                          <span className="ceiling-numbers">
                            {population.toLocaleString()} / {maxPopulation.toLocaleString()}
                          </span>
                        </div>
                        <div className="ceiling-bar layered">
                          <div
                            className="ceiling-fill population"
                            style={{ width: `${populationPct}%` }}
                          />
                          <div
                            className="ceiling-fill workforce-overlay"
                            style={{ width: `${workforceWithinPopPct}%` }}
                            title="Workforce share of total population"
                          />
                        </div>
                      </div>
                    ) : (
                      <div className="ceiling-unavailable">
                        Population ceiling unavailable — habitability data missing
                      </div>
                    )}
                  </div>
                  {growth !== null && habScore !== null && (
                    <div className="growth-line">
                      ≈ +{Math.round(growth).toLocaleString()}/day at current habitability ({habScore}/100)
                    </div>
                  )}
                  {typeof extended.morale === 'number' && (
                    <div className="morale-line" title="Colony morale — drops under siege; planet becomes vulnerable at 0">
                      Morale: <span className={`morale-value ${extended.morale <= 25 ? 'critical' : extended.morale <= 50 ? 'low' : 'good'}`}>
                        {extended.morale}%
                      </span>
                    </div>
                  )}
                </div>
              );
            })()}
            <div className="overview-section">
              <h3>Colony Information</h3>
              <div className="info-grid">
                <div className="info-item">
                  <span className="label">Type:</span>
                  <span className="value">{selectedPlanet.planetType}</span>
                </div>
                <div className="info-item">
                  <span className="label">Location:</span>
                  <span className="value">{selectedPlanet.sectorName}</span>
                </div>
                <div className="info-item">
                  <span className="label">Specialization:</span>
                  <span className="value">
                    {getSpecializationIcon(selectedPlanet.specialization)} 
                    {selectedPlanet.specialization || 'None'}
                  </span>
                </div>
                <div className="info-item">
                  <span className="label">Workforce:</span>
                  <span className="value">
                    {selectedPlanet.colonists.toLocaleString()} / {selectedPlanet.maxColonists.toLocaleString()}
                  </span>
                </div>
              </div>
            </div>

            <div className="overview-section">
              <h3>Production Rates</h3>
              <div className="production-grid">
                <div className="production-item">
                  <span className="resource-icon">⛽</span>
                  <span className="resource-name">Fuel</span>
                  <span className="resource-value">{selectedPlanet.productionRates.fuel}/day</span>
                </div>
                <div className="production-item">
                  <span className="resource-icon">🌿</span>
                  <span className="resource-name">Organics</span>
                  <span className="resource-value">{selectedPlanet.productionRates.organics}/day</span>
                </div>
                <div className="production-item">
                  <span className="resource-icon">⚙️</span>
                  <span className="resource-name">Equipment</span>
                  <span className="resource-value">{selectedPlanet.productionRates.equipment}/day</span>
                </div>
                <div className="production-item">
                  <span className="resource-icon">👥</span>
                  <span className="resource-name">Colonists</span>
                  <span className="resource-value">+{selectedPlanet.productionRates.colonists}/day</span>
                </div>
              </div>
            </div>

            <div className="overview-section">
              <h3>Colonist Assignments</h3>
              <div className="allocation-bars">
                {(() => {
                  const total = Math.max(1, selectedPlanet.colonists);
                  const pct = (heads: number) => Math.min(100, (Math.max(0, heads) / total) * 100);
                  const rows = [
                    { key: 'fuel', label: '⛽ Fuel Production', heads: selectedPlanet.allocations.fuel },
                    { key: 'organics', label: '🌿 Organics Production', heads: selectedPlanet.allocations.organics },
                    { key: 'equipment', label: '⚙️ Equipment Production', heads: selectedPlanet.allocations.equipment },
                    { key: 'unused', label: '💤 Idle', heads: selectedPlanet.allocations.unused },
                  ];
                  return rows.map(row => (
                    <div className="allocation-item" key={row.key}>
                      <span className="allocation-label">{row.label}</span>
                      <div className="allocation-bar">
                        <div
                          className={`allocation-fill ${row.key}`}
                          style={{ width: `${pct(row.heads)}%` }}
                        />
                        <span className="allocation-value">
                          {Math.max(0, row.heads).toLocaleString()} colonists
                        </span>
                      </div>
                    </div>
                  ));
                })()}
              </div>
            </div>

            <div className="overview-section">
              <h3>Buildings</h3>
              <div className="buildings-grid">
                {selectedPlanet.buildings.map(building => (
                  <div key={building.type} className={`building-item ${building.upgrading ? 'upgrading' : ''}`}>
                    <div className="building-icon">
                      {building.type === 'factory' && '🏭'}
                      {building.type === 'farm' && '🌾'}
                      {building.type === 'mine' && '⛏️'}
                      {building.type === 'defense' && '🛡️'}
                      {building.type === 'research' && '🔬'}
                    </div>
                    <div className="building-info">
                      <span className="building-name">{building.type}</span>
                      <span className="building-level">Level {building.level}</span>
                    </div>
                    {building.upgrading && (
                      <div className="upgrade-progress">
                        <div className="progress-bar">
                          <div className="progress-fill" style={{ width: '30%' }} />
                        </div>
                        <span className="upgrade-time">Upgrading...</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div className="overview-section">
              <h3>Planetary Defenses</h3>
              <div className="defense-grid">
                <div className="defense-item">
                  <span className="defense-icon">🔫</span>
                  <span className="defense-name">Turrets</span>
                  <span className="defense-value">{selectedPlanet.defenses.turrets}</span>
                </div>
                <div className="defense-item">
                  <span className="defense-icon">🛡️</span>
                  <span className="defense-name">Shields</span>
                  <span className="defense-value">{selectedPlanet.defenses.shields}</span>
                </div>
                <div className="defense-item">
                  <span className="defense-icon">✈️</span>
                  <span className="defense-name">Drones</span>
                  <span className="defense-value">{selectedPlanet.defenses.drones}</span>
                </div>
              </div>
            </div>

            {selectedPlanet.underSiege && selectedPlanet.siegeDetails && (
              <div className="overview-section siege-section">
                <h3>Siege Status</h3>
                <div className="siege-details">
                  <div className="siege-info">
                    <span className="label">Attacker:</span>
                    <span className="value">{selectedPlanet.siegeDetails.attackerName}</span>
                  </div>
                  <div className="siege-info">
                    <span className="label">Phase:</span>
                    <span className="value phase-{selectedPlanet.siegeDetails.phase}">
                      {selectedPlanet.siegeDetails.phase.toUpperCase()}
                    </span>
                  </div>
                  <div className="siege-info">
                    <span className="label">Defense Effectiveness:</span>
                    <span className="value">{selectedPlanet.siegeDetails.defenseEffectiveness}%</span>
                  </div>
                  {selectedPlanet.siegeDetails.casualties && (
                    <div className="siege-casualties">
                      <span className="label">Casualties:</span>
                      <span className="casualty">
                        👥 {selectedPlanet.siegeDetails.casualties.colonists} colonists
                      </span>
                      <span className="casualty">
                        ✈️ {selectedPlanet.siegeDetails.casualties.drones} drones
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
          )}

          <div className="planet-actions">
            <button
              className="action-button allocate"
              onClick={() => setShowAllocator(true)}
              title="Assign colonists to production roles"
            >
              📊 Manage Allocations
            </button>
            <button
              className="action-button upgrade"
              onClick={() => setShowBuildingManager(true)}
              title="Upgrade planetary buildings"
            >
              🔨 Upgrade Buildings
            </button>
            <button
              className="action-button defense"
              onClick={() => setShowDefenseConfig(true)}
              title="Configure turrets, shields, and drones"
            >
              🛡️ Configure Defenses
            </button>
            <button
              className="action-button specialize"
              onClick={() => setShowSpecialization(true)}
              title="Choose a colony specialization"
            >
              🎯 Set Specialization
            </button>
          </div>
        </div>
      )}

      {/* Colonist Allocator Modal */}
      {showAllocator && selectedPlanet && (
        <div className="modal-overlay" onClick={() => setShowAllocator(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <ColonistAllocator
              planet={selectedPlanet}
              onUpdate={handlePlanetUpdate}
              onClose={() => setShowAllocator(false)}
            />
          </div>
        </div>
      )}

      {/* Building Manager Modal */}
      {showBuildingManager && selectedPlanet && (
        <div className="modal-overlay" onClick={() => setShowBuildingManager(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <BuildingManager
              planet={selectedPlanet}
              onUpdate={handlePlanetUpdate}
              onClose={() => setShowBuildingManager(false)}
            />
          </div>
        </div>
      )}

      {/* Defense Configuration Modal */}
      {showDefenseConfig && selectedPlanet && (
        <div className="modal-overlay" onClick={() => setShowDefenseConfig(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <DefenseConfiguration
              planet={selectedPlanet}
              onUpdate={handlePlanetUpdate}
              onClose={() => setShowDefenseConfig(false)}
            />
          </div>
        </div>
      )}

      {/* Genesis Deployment Modal */}
      {showGenesisDeployment && (
        <div className="modal-overlay" onClick={() => setShowGenesisDeployment(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <GenesisDeployment
              onSuccess={() => {
                setShowGenesisDeployment(false);
                loadPlanets();
              }}
              onClose={() => setShowGenesisDeployment(false)}
            />
          </div>
        </div>
      )}

      {/* Colony Specialization Modal */}
      {showSpecialization && selectedPlanet && (
        <div className="modal-overlay" onClick={() => setShowSpecialization(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <ColonySpecializationComponent
              planet={selectedPlanet}
              onUpdate={handlePlanetUpdate}
              onClose={() => setShowSpecialization(false)}
            />
          </div>
        </div>
      )}

      {/* Siege Status Monitor Modal */}
      {showSiegeMonitor && selectedPlanet && (
        <div className="modal-overlay" onClick={() => setShowSiegeMonitor(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <SiegeStatusMonitor
              planet={selectedPlanet}
              onUpdate={handlePlanetUpdate}
              onClose={() => setShowSiegeMonitor(false)}
            />
          </div>
        </div>
      )}
    </div>
    </ColonialShell>
  );
};