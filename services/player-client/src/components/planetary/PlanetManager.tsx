import React, { useState, useEffect } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet, ColonySpecialization } from '../../types/planetary';
import { ColonistAllocator } from './ColonistAllocator';
import { BuildingManager } from './BuildingManager';
import { DefenseConfiguration } from './DefenseConfiguration';
import { GenesisDeployment } from './GenesisDeployment';
import { ColonySpecialization as ColonySpecializationComponent } from './ColonySpecialization';
import { SiegeStatusMonitor } from './SiegeStatusMonitor';
import GameLayout from '../layouts/GameLayout';
import './planet-manager.css';

export const PlanetManager: React.FC = () => {
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

  useEffect(() => {
    loadPlanets();
  }, []);

  const loadPlanets = async () => {
    try {
      setError(null);
      const response = await gameAPI.planetary.getOwnedPlanets();
      setPlanets(response.planets || []);
      
      // Select first planet by default
      if (response.planets && response.planets.length > 0 && !selectedPlanet) {
        setSelectedPlanet(response.planets[0]);
      }
    } catch (err) {
      setError('Failed to load planets');
      console.error('Error loading planets:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const handlePlanetSelect = (planet: Planet) => {
    setSelectedPlanet(planet);
  };

  const handleRefresh = () => {
    setRefreshing(true);
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
    const icons = {
      terran: '🌍',
      oceanic: '🌊',
      mountainous: '⛰️',
      desert: '🏜️',
      frozen: '❄️'
    };
    return icons[type as keyof typeof icons] || '🪐';
  };

  if (loading) {
    return (
      <GameLayout>
        <div className="planet-manager loading">
          <div className="loading-spinner">Loading planetary data...</div>
        </div>
      </GameLayout>
    );
  }

  if (error) {
    return (
      <GameLayout>
        <div className="planet-manager error">
          <div className="error-message">
            <span className="error-icon">⚠️</span>
            {error}
            <button onClick={handleRefresh} className="retry-button">
              Retry
            </button>
          </div>
        </div>
      </GameLayout>
    );
  }

  if (planets.length === 0) {
    return (
      <GameLayout>
      <div className="planet-manager empty">
        <div className="empty-state">
          <h2>No Planets Owned</h2>
          <p>You don't own any planets yet. Deploy a Genesis Device to create your first colony!</p>
          <button 
            className="genesis-button"
            onClick={() => setShowGenesisDeployment(true)}
          >
            🌌 Deploy Genesis Device
          </button>
        </div>
        
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
      </GameLayout>
    );
  }

  return (
    <GameLayout>
    <div className="planet-manager">
      {/* Planet List Sidebar */}
      <div className="planet-list">
        <div className="planet-list-header">
          <h3>Your Colonies ({planets.length})</h3>
          <div className="header-actions">
            <button 
              onClick={() => setShowGenesisDeployment(true)} 
              className="genesis-mini-button"
              title="Deploy Genesis Device"
            >
              🌌
            </button>
            <button 
              onClick={handleRefresh} 
              className="refresh-button"
              disabled={refreshing}
              title="Refresh planet data"
            >
              {refreshing ? '🔄' : '🔃'}
            </button>
          </div>
        </div>
        
        <div className="planet-items">
          {planets.map(planet => (
            <div
              key={planet.id}
              className={`planet-item ${selectedPlanet?.id === planet.id ? 'selected' : ''} ${planet.underSiege ? 'under-siege' : ''}`}
              onClick={() => handlePlanetSelect(planet)}
            >
              <div className="planet-item-header">
                <span className="planet-icon">
                  {getPlanetTypeIcon(planet.planetType)}
                </span>
                <span className="planet-name">{planet.name}</span>
                {planet.underSiege && <span className="siege-indicator">🚨</span>}
              </div>
              
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
            </div>
          ))}
        </div>
      </div>

      {/* Planet Details */}
      {selectedPlanet && (
        <div className="planet-details">
          <div className="planet-header">
            <h2>
              {getPlanetTypeIcon(selectedPlanet.planetType)} {selectedPlanet.name}
            </h2>
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
          </div>

          <div className="planet-overview">
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
                  <span className="label">Population:</span>
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
              <h3>Resource Allocations</h3>
              <div className="allocation-bars">
                <div className="allocation-item">
                  <span className="allocation-label">⛽ Fuel Production</span>
                  <div className="allocation-bar">
                    <div 
                      className="allocation-fill fuel"
                      style={{ width: `${selectedPlanet.allocations.fuel}%` }}
                    />
                    <span className="allocation-value">{selectedPlanet.allocations.fuel}%</span>
                  </div>
                </div>
                <div className="allocation-item">
                  <span className="allocation-label">🌿 Organics Production</span>
                  <div className="allocation-bar">
                    <div 
                      className="allocation-fill organics"
                      style={{ width: `${selectedPlanet.allocations.organics}%` }}
                    />
                    <span className="allocation-value">{selectedPlanet.allocations.organics}%</span>
                  </div>
                </div>
                <div className="allocation-item">
                  <span className="allocation-label">⚙️ Equipment Production</span>
                  <div className="allocation-bar">
                    <div 
                      className="allocation-fill equipment"
                      style={{ width: `${selectedPlanet.allocations.equipment}%` }}
                    />
                    <span className="allocation-value">{selectedPlanet.allocations.equipment}%</span>
                  </div>
                </div>
                <div className="allocation-item">
                  <span className="allocation-label">💤 Unallocated</span>
                  <div className="allocation-bar">
                    <div 
                      className="allocation-fill unused"
                      style={{ width: `${selectedPlanet.allocations.unused}%` }}
                    />
                    <span className="allocation-value">{selectedPlanet.allocations.unused}%</span>
                  </div>
                </div>
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

          <div className="planet-actions">
            <button 
              className="action-button allocate"
              onClick={() => setShowAllocator(true)}
            >
              📊 Manage Allocations
            </button>
            <button 
              className="action-button upgrade"
              onClick={() => setShowBuildingManager(true)}
            >
              🔨 Upgrade Buildings
            </button>
            <button 
              className="action-button defense"
              onClick={() => setShowDefenseConfig(true)}
            >
              🛡️ Configure Defenses
            </button>
            <button 
              className="action-button specialize"
              onClick={() => setShowSpecialization(true)}
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
    </GameLayout>
  );
};