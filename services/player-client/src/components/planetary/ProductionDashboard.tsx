import React, { useState, useEffect, useMemo } from 'react';
import type { Planet, ProductionRates } from '../../types/planetary';
import './production-dashboard.css';

interface ProductionDashboardProps {
  planets: Planet[];
  onPlanetSelect?: (planet: Planet) => void;
}

interface ProductionMetrics {
  totalFuel: number;
  totalOrganics: number;
  totalEquipment: number;
  totalColonists: number;
  totalPlanets: number;
  averageEfficiency: number;
  topProducers: {
    fuel: Planet | null;
    organics: Planet | null;
    equipment: Planet | null;
  };
}

export const ProductionDashboard: React.FC<ProductionDashboardProps> = ({ 
  planets, 
  onPlanetSelect 
}) => {
  const [selectedResource, setSelectedResource] = useState<keyof ProductionRates>('fuel');
  const [sortBy, setSortBy] = useState<'name' | 'production' | 'efficiency'>('production');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Calculate production metrics
  const metrics = useMemo<ProductionMetrics>(() => {
    const totalPlanets = planets.length;
    
    if (totalPlanets === 0) {
      return {
        totalFuel: 0,
        totalOrganics: 0,
        totalEquipment: 0,
        totalColonists: 0,
        totalPlanets: 0,
        averageEfficiency: 0,
        topProducers: {
          fuel: null,
          organics: null,
          equipment: null
        }
      };
    }

    const totals = planets.reduce((acc, planet) => {
      acc.fuel += planet.productionRates.fuel;
      acc.organics += planet.productionRates.organics;
      acc.equipment += planet.productionRates.equipment;
      acc.colonists += planet.productionRates.colonists;
      // Efficiency = % of colonists assigned to production. `unused` is a raw
      // colonist COUNT, so the share is (colonists - unused) / colonists.
      acc.efficiency += planet.colonists > 0
        ? (100 * (planet.colonists - planet.allocations.unused)) / planet.colonists
        : 0;
      return acc;
    }, { fuel: 0, organics: 0, equipment: 0, colonists: 0, efficiency: 0 });

    const topProducers = {
      fuel: planets.reduce((max, p) => 
        p.productionRates.fuel > (max?.productionRates.fuel || 0) ? p : max, null as Planet | null),
      organics: planets.reduce((max, p) => 
        p.productionRates.organics > (max?.productionRates.organics || 0) ? p : max, null as Planet | null),
      equipment: planets.reduce((max, p) => 
        p.productionRates.equipment > (max?.productionRates.equipment || 0) ? p : max, null as Planet | null)
    };

    return {
      totalFuel: totals.fuel,
      totalOrganics: totals.organics,
      totalEquipment: totals.equipment,
      totalColonists: totals.colonists,
      totalPlanets,
      averageEfficiency: Math.round(totals.efficiency / totalPlanets),
      topProducers
    };
  }, [planets]);

  // Sort planets based on current criteria
  const sortedPlanets = useMemo(() => {
    const sorted = [...planets].sort((a, b) => {
      let aValue: number | string = 0;
      let bValue: number | string = 0;

      switch (sortBy) {
        case 'name':
          aValue = a.name.toLowerCase();
          bValue = b.name.toLowerCase();
          break;
        case 'production':
          aValue = a.productionRates[selectedResource];
          bValue = b.productionRates[selectedResource];
          break;
        case 'efficiency':
          aValue = a.colonists > 0 ? (100 * (a.colonists - a.allocations.unused)) / a.colonists : 0;
          bValue = b.colonists > 0 ? (100 * (b.colonists - b.allocations.unused)) / b.colonists : 0;
          break;
      }

      if (typeof aValue === 'string' && typeof bValue === 'string') {
        return sortOrder === 'asc' ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue);
      }
      
      return sortOrder === 'asc' ? 
        (aValue as number) - (bValue as number) : 
        (bValue as number) - (aValue as number);
    });

    return sorted;
  }, [planets, sortBy, sortOrder, selectedResource]);

  const handleSort = (newSortBy: typeof sortBy) => {
    if (sortBy === newSortBy) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(newSortBy);
      setSortOrder('desc');
    }
  };

  const getResourceIcon = (resource: keyof ProductionRates) => {
    const icons = {
      fuel: '⛽',
      organics: '🌿',
      equipment: '⚙️',
      colonists: '👥'
    };
    return icons[resource];
  };

  const getResourceColor = (resource: keyof ProductionRates) => {
    const colors = {
      fuel: '#ff6b6b',
      organics: '#51cf66',
      equipment: '#339af0',
      colonists: '#f59f00'
    };
    return colors[resource];
  };

  const formatNumber = (num: number) => {
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
    return num.toString();
  };

  const getEfficiencyClass = (efficiency: number) => {
    if (efficiency >= 90) return 'excellent';
    if (efficiency >= 75) return 'good';
    if (efficiency >= 50) return 'fair';
    return 'poor';
  };

  if (planets.length === 0) {
    return (
      <div className="production-dashboard empty">
        <div className="empty-state">
          <h2>No Production Data</h2>
          <p>You need to own planets to see production statistics.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="production-dashboard">
      <div className="dashboard-header">
        <h2>Empire Production Dashboard</h2>
        <div className="header-stats">
          <span className="stat">
            <span className="stat-icon">🌍</span>
            {metrics.totalPlanets} Planets
          </span>
          <span className="stat">
            <span className="stat-icon">⚡</span>
            {metrics.averageEfficiency}% Avg Efficiency
          </span>
        </div>
      </div>

      <div className="metrics-overview">
        <div className="metric-card fuel">
          <div className="metric-icon">{getResourceIcon('fuel')}</div>
          <div className="metric-content">
            <h3>Total Fuel Production</h3>
            <div className="metric-value">{formatNumber(metrics.totalFuel)}</div>
            <div className="metric-unit">units/day</div>
            {metrics.topProducers.fuel && (
              <div className="top-producer">
                Top: {metrics.topProducers.fuel.name} ({metrics.topProducers.fuel.productionRates.fuel}/day)
              </div>
            )}
          </div>
        </div>

        <div className="metric-card organics">
          <div className="metric-icon">{getResourceIcon('organics')}</div>
          <div className="metric-content">
            <h3>Total Organics Production</h3>
            <div className="metric-value">{formatNumber(metrics.totalOrganics)}</div>
            <div className="metric-unit">units/day</div>
            {metrics.topProducers.organics && (
              <div className="top-producer">
                Top: {metrics.topProducers.organics.name} ({metrics.topProducers.organics.productionRates.organics}/day)
              </div>
            )}
          </div>
        </div>

        <div className="metric-card equipment">
          <div className="metric-icon">{getResourceIcon('equipment')}</div>
          <div className="metric-content">
            <h3>Total Equipment Production</h3>
            <div className="metric-value">{formatNumber(metrics.totalEquipment)}</div>
            <div className="metric-unit">units/day</div>
            {metrics.topProducers.equipment && (
              <div className="top-producer">
                Top: {metrics.topProducers.equipment.name} ({metrics.topProducers.equipment.productionRates.equipment}/day)
              </div>
            )}
          </div>
        </div>

        <div className="metric-card colonists">
          <div className="metric-icon">{getResourceIcon('colonists')}</div>
          <div className="metric-content">
            <h3>Total Population Growth</h3>
            <div className="metric-value">+{formatNumber(metrics.totalColonists)}</div>
            <div className="metric-unit">colonists/day</div>
          </div>
        </div>
      </div>

      <div className="resource-selector">
        <h3>Resource Focus</h3>
        <div className="resource-buttons">
          {(['fuel', 'organics', 'equipment'] as const).map(resource => (
            <button
              key={resource}
              className={`resource-button ${selectedResource === resource ? 'active' : ''}`}
              onClick={() => setSelectedResource(resource)}
              style={{
                borderColor: selectedResource === resource ? getResourceColor(resource) : 'transparent'
              }}
            >
              <span className="resource-icon">{getResourceIcon(resource)}</span>
              <span className="resource-name">{resource}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="planets-table">
        <div className="table-header">
          <h3>Planet Production Details</h3>
          <div className="sort-controls">
            <button 
              className={`sort-button ${sortBy === 'name' ? 'active' : ''}`}
              onClick={() => handleSort('name')}
            >
              Name {sortBy === 'name' && (sortOrder === 'asc' ? '↑' : '↓')}
            </button>
            <button 
              className={`sort-button ${sortBy === 'production' ? 'active' : ''}`}
              onClick={() => handleSort('production')}
            >
              Production {sortBy === 'production' && (sortOrder === 'asc' ? '↑' : '↓')}
            </button>
            <button 
              className={`sort-button ${sortBy === 'efficiency' ? 'active' : ''}`}
              onClick={() => handleSort('efficiency')}
            >
              Efficiency {sortBy === 'efficiency' && (sortOrder === 'asc' ? '↑' : '↓')}
            </button>
          </div>
        </div>

        <div className="planets-list">
          {sortedPlanets.map(planet => {
            const efficiency = 100 - planet.allocations.unused;
            const production = planet.productionRates[selectedResource];
            const maxProduction = Math.max(...Object.values(planet.productionRates));
            const productionPercent = maxProduction > 0 ? (production / maxProduction) * 100 : 0;

            return (
              <div
                key={planet.id}
                className={`planet-row ${planet.underSiege ? 'under-siege' : ''}`}
                onClick={() => onPlanetSelect?.(planet)}
              >
                <div className="planet-info">
                  <h4>{planet.name}</h4>
                  <span className="planet-sector">{planet.sectorName}</span>
                </div>

                <div className="production-stats">
                  <div className="production-bar-container">
                    <div className="production-labels">
                      <span className="production-label">
                        {getResourceIcon(selectedResource)} {selectedResource}:
                      </span>
                      <span className="production-value">{production}/day</span>
                    </div>
                    <div className="production-bar">
                      <div 
                        className="production-fill"
                        style={{
                          width: `${productionPercent}%`,
                          backgroundColor: getResourceColor(selectedResource)
                        }}
                      />
                    </div>
                  </div>

                  <div className="efficiency-container">
                    <span className="efficiency-label">Efficiency:</span>
                    <span className={`efficiency-value ${getEfficiencyClass(efficiency)}`}>
                      {efficiency}%
                    </span>
                  </div>

                  <div className="allocation-summary">
                    <span className="allocation-item">
                      {getResourceIcon('fuel')} {planet.allocations.fuel}%
                    </span>
                    <span className="allocation-item">
                      {getResourceIcon('organics')} {planet.allocations.organics}%
                    </span>
                    <span className="allocation-item">
                      {getResourceIcon('equipment')} {planet.allocations.equipment}%
                    </span>
                  </div>
                </div>

                <div className="planet-status">
                  {planet.underSiege && (
                    <span className="status-badge siege">Under Siege</span>
                  )}
                  {planet.specialization && (
                    <span className="status-badge specialization">
                      {planet.specialization}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="production-tips">
        <h3>Production Tips</h3>
        <div className="tips-grid">
          <div className="tip">
            <span className="tip-icon">💡</span>
            <p>Planets with less than 75% efficiency are wasting colonist potential.</p>
          </div>
          <div className="tip">
            <span className="tip-icon">🎯</span>
            <p>Specialize planets based on their type for maximum production bonuses.</p>
          </div>
          <div className="tip">
            <span className="tip-icon">⚖️</span>
            <p>Balance your empire's total production to avoid resource shortages.</p>
          </div>
          <div className="tip">
            <span className="tip-icon">🛡️</span>
            <p>Planets under siege have reduced production. Defend them quickly!</p>
          </div>
        </div>
      </div>
    </div>
  );
};