import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useAdmin } from '../../contexts/AdminContext';
import SectorDetail from '../universe/SectorDetail';
import PortDetail from '../universe/StationDetail';  
import PlanetDetail from '../universe/PlanetDetail';
import './universe-manager.css';

interface ViewState {
  type: 'overview' | 'sector' | 'port' | 'planet';
  data?: any;
}

const UniverseManager: React.FC = () => {
  const {
    galaxyState,
    regions,
    sectors,
    loadGalaxyInfo,
    loadSectors,
    loadRegions,
    clearGalaxyData,
    isLoading,
    error
  } = useAdmin();

  const [viewState, setViewState] = useState<ViewState>({ type: 'overview' });
  const [activeTab, setActiveTab] = useState<'galaxy' | 'sectors' | 'map'>('galaxy');
  const [showGalaxyGenerator, setShowGalaxyGenerator] = useState(false);
  const [selectedSector, setSelectedSector] = useState<any>(null);
  
  // Galaxy map state
  const [mapOffset, setMapOffset] = useState({ x: 0, y: 0 });
  const [mapZoom, setMapZoom] = useState(1);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [hoveredSector, setHoveredSector] = useState<any>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const mapContainerRef = useRef<HTMLDivElement>(null);

  // Galaxy configuration state
  const [galaxyConfig, setGalaxyConfig] = useState({
    name: 'New Galaxy',
    density: {
      station_density: 10,      // 10% of sectors
      planet_density: 15,    // 15% of sectors
      one_way_warp_percentage: 5
    },
    warp_tunnel_config: {
      min_per_region: 5,
      max_per_region: 15,
      stability_range: { min: 70, max: 100 }
    },
    resource_distribution: 'balanced' as 'balanced' | 'clustered' | 'random',
    hazard_levels: 'moderate' as 'low' | 'moderate' | 'high' | 'extreme',
    connectivity: 'normal' as 'sparse' | 'normal' | 'dense'
  });

  // Load data on mount
  useEffect(() => {
    loadGalaxyInfo();
  }, []);

  useEffect(() => {
    if (galaxyState) {
      loadRegions();
      loadSectors();
    }
  }, [galaxyState]);

  // Handle sector click
  const handleSectorClick = (sector: any) => {
    setSelectedSector(sector);
    setViewState({ type: 'sector', data: sector });
  };

  // Handle port click from sector detail
  const handlePortClick = (portData: any) => {
    setViewState({ type: 'port', data: portData });
  };

  // Handle planet click from sector detail
  const handlePlanetClick = (planetData: any) => {
    setViewState({ type: 'planet', data: planetData });
  };

  // Handle back navigation
  const handleBack = () => {
    if (viewState.type === 'port' || viewState.type === 'planet') {
      setViewState({ type: 'sector', data: selectedSector });
    } else {
      setViewState({ type: 'overview' });
      setSelectedSector(null);
    }
  };

  // Legacy handleGenerateGalaxy removed in bang cutover (Phase 4A).
  // Generation now lives at /universe/bang (BangGalaxyPage).

  // Render galaxy configuration
  const renderGalaxyConfig = () => (
    <div className="galaxy-config-panel">
      <h3>🌌 Bang a New Galaxy Into Existence!</h3>
      
      <div className="config-section">
        <h4>Basic Settings</h4>
        <div className="form-group">
          <label>Galaxy Name</label>
          <input
            type="text"
            value={galaxyConfig.name}
            onChange={(e) => setGalaxyConfig({...galaxyConfig, name: e.target.value})}
            placeholder="Enter galaxy name"
          />
        </div>
      </div>

      <div className="config-section info-box">
        <h4>🌌 Galaxy Structure</h4>
        <div className="info-text">
          <p><strong>Central Nexus:</strong> 5000 sectors • 1 zone ("The Expanse")</p>
          <p><strong>Terran Space:</strong> 300 sectors • 3 zones (Federation/Border/Frontier)</p>
          <p className="text-muted">Zones and regions are automatically configured</p>
        </div>
      </div>

      <div className="config-section">
        <h4>Density Settings</h4>
        
        <div className="form-group">
          <label>Port Density: {galaxyConfig.density.station_density}%</label>
          <input 
            type="range" 
            min="5" 
            max="15" 
            value={galaxyConfig.density.station_density}
            onChange={(e) => setGalaxyConfig({
              ...galaxyConfig, 
              density: {
                ...galaxyConfig.density,
                station_density: parseInt(e.target.value)
              }
            })}
          />
          <div className="info-text">~{Math.floor(5300 * galaxyConfig.density.station_density / 100)} ports</div>
        </div>

        <div className="form-group">
          <label>Planet Density: {galaxyConfig.density.planet_density}%</label>
          <input
            type="range"
            min="2"
            max="25"
            value={galaxyConfig.density.planet_density}
            onChange={(e) => setGalaxyConfig({
              ...galaxyConfig,
              density: {
                ...galaxyConfig.density,
                planet_density: parseInt(e.target.value)
              }
            })}
          />
          <div className="info-text">~{Math.floor(5300 * galaxyConfig.density.planet_density / 100)} planets</div>
        </div>
        
        <div className="form-group">
          <label>One-Way Warp Percentage: {galaxyConfig.density.one_way_warp_percentage}%</label>
          <input 
            type="range" 
            min="2" 
            max="8" 
            value={galaxyConfig.density.one_way_warp_percentage}
            onChange={(e) => setGalaxyConfig({
              ...galaxyConfig, 
              density: {
                ...galaxyConfig.density,
                one_way_warp_percentage: parseInt(e.target.value)
              }
            })}
          />
        </div>
      </div>

      <div className="config-section">
        <h4>Warp Tunnel Configuration</h4>
        
        <div className="form-group">
          <label>Min Warps per Region: {galaxyConfig.warp_tunnel_config.min_per_region}</label>
          <input 
            type="range" 
            min="1" 
            max="10" 
            value={galaxyConfig.warp_tunnel_config.min_per_region}
            onChange={(e) => setGalaxyConfig({
              ...galaxyConfig, 
              warp_tunnel_config: {
                ...galaxyConfig.warp_tunnel_config,
                min_per_region: parseInt(e.target.value)
              }
            })}
          />
        </div>
        
        <div className="form-group">
          <label>Max Warps per Region: {galaxyConfig.warp_tunnel_config.max_per_region}</label>
          <input 
            type="range" 
            min="10" 
            max="30" 
            value={galaxyConfig.warp_tunnel_config.max_per_region}
            onChange={(e) => setGalaxyConfig({
              ...galaxyConfig, 
              warp_tunnel_config: {
                ...galaxyConfig.warp_tunnel_config,
                max_per_region: parseInt(e.target.value)
              }
            })}
          />
        </div>
        
        <div className="form-group">
          <label>Stability Range: {galaxyConfig.warp_tunnel_config.stability_range.min}% - {galaxyConfig.warp_tunnel_config.stability_range.max}%</label>
          <div className="dual-slider">
            <input 
              type="range" 
              min="50" 
              max="100" 
              value={galaxyConfig.warp_tunnel_config.stability_range.min}
              onChange={(e) => setGalaxyConfig({
                ...galaxyConfig, 
                warp_tunnel_config: {
                  ...galaxyConfig.warp_tunnel_config,
                  stability_range: {
                    ...galaxyConfig.warp_tunnel_config.stability_range,
                    min: parseInt(e.target.value)
                  }
                }
              })}
            />
            <input 
              type="range" 
              min="50" 
              max="100" 
              value={galaxyConfig.warp_tunnel_config.stability_range.max}
              onChange={(e) => setGalaxyConfig({
                ...galaxyConfig, 
                warp_tunnel_config: {
                  ...galaxyConfig.warp_tunnel_config,
                  stability_range: {
                    ...galaxyConfig.warp_tunnel_config.stability_range,
                    max: parseInt(e.target.value)
                  }
                }
              })}
            />
          </div>
        </div>
      </div>

      <div className="form-actions">
        <button
          className="btn btn-primary btn-lg"
          onClick={handleGenerateGalaxy}
          disabled={isLoading}
        >
          {isLoading ? '🌌 Creating Galaxy...' : '💥 Bang a New Galaxy!'}
        </button>
        {galaxyState && (
          <button 
            className="btn btn-secondary"
            onClick={() => setShowGalaxyGenerator(false)}
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );

  // Render galaxy overview
  const renderGalaxyOverview = () => (
    <div className="galaxy-overview">
      <div className="galaxy-header">
        <h2>{galaxyState?.name || 'No Universe'}</h2>
        {galaxyState && (
          <Link to="/universe/bang" className="btn btn-outline">
            💥 Bang a New Galaxy!
          </Link>
        )}
      </div>

      {galaxyState ? (
        <div className="galaxy-stats">
          <div className="stats-grid">
            <Link to="/universe/sectors" className="stat-card clickable-stat-card">
              <div className="stat-icon">🔲</div>
              <h3>Total Sectors</h3>
              <div className="stat-value">{galaxyState.statistics.total_sectors}</div>
              <div className="stat-detail">
                {galaxyState.statistics.discovered_sectors} discovered
              </div>
            </Link>
            <Link to="/universe/stations" className="stat-card clickable-stat-card">
              <div className="stat-icon">🏪</div>
              <h3>Stations</h3>
              <div className="stat-value">{galaxyState.statistics.station_count || 0}</div>
              <div className="stat-detail">
                {galaxyState.statistics.total_sectors > 0
                  ? Math.round((galaxyState.statistics.station_count || 0) / galaxyState.statistics.total_sectors * 100)
                  : 0}% of sectors
              </div>
            </Link>
            <Link to="/universe/planets" className="stat-card clickable-stat-card">
              <div className="stat-icon">🌍</div>
              <h3>Planets</h3>
              <div className="stat-value">{galaxyState.statistics.planet_count || 0}</div>
              <div className="stat-detail">
                {galaxyState.statistics.total_sectors > 0
                  ? Math.round((galaxyState.statistics.planet_count || 0) / galaxyState.statistics.total_sectors * 100)
                  : 0}% of sectors
              </div>
            </Link>
            <div className="stat-card" title="The natural sector-to-sector adjacency graph that players actually traverse. Generated by bang as part of the galaxy.">
              <div className="stat-icon">🌀</div>
              <h3>Sector Warps</h3>
              <div className="stat-value">{(galaxyState.statistics.sector_warp_count ?? 0).toLocaleString()}</div>
              <div className="stat-detail">
                Natural navigation graph
              </div>
            </div>
            <Link to="/universe/warptunnels" className="stat-card clickable-stat-card" title="Special long-range tunnels: cross-region gates from generation + any premium/quantum/player-built tunnels added later.">
              <div className="stat-icon">✨</div>
              <h3>Special Tunnels</h3>
              <div className="stat-value">{galaxyState.statistics.warp_tunnel_count}</div>
              <div className="stat-detail">
                Cross-region &amp; artificial
              </div>
            </Link>
          </div>

          <div className="region-distribution">
            <h3>Galaxy Regions</h3>
            <div className="region-list">
              {regions.map((region: any) => (
                <div key={region.id} className="region-item">
                  <div className="region-header">
                    <span className="region-name">{region.display_name}</span>
                    <span className={`region-badge ${
                      region.region_type === 'central_nexus' ? 'badge-primary' :
                      region.region_type === 'terran_space' ? 'badge-info' :
                      'badge-success'
                    }`}>
                      {region.region_type?.replace('_', ' ') || 'Unknown'}
                    </span>
                  </div>
                  <div className="region-stats">
                    <span>{region.total_sectors} sectors</span>
                    {region.statistics && (
                      <span>• {region.statistics.discovered_sectors} discovered</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="no-galaxy">
          <p>No universe exists yet. Bang one into existence to begin!</p>
          <Link to="/universe/bang" className="btn btn-primary btn-lg">
            💥 Bang a New Galaxy!
          </Link>
        </div>
      )}
    </div>
  );

  // Galaxy map event handlers
  const handleMapWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setMapZoom(prev => Math.max(0.1, Math.min(10, prev * delta)));
  }, []);

  const handleMapMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button === 0) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - mapOffset.x, y: e.clientY - mapOffset.y });
    }
  }, [mapOffset]);

  const handleMapMouseMove = useCallback((e: React.MouseEvent) => {
    if (isDragging) {
      setMapOffset({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y
      });
    }
  }, [isDragging, dragStart]);

  const handleMapMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  // Render galaxy map visualization
  const renderGalaxyMap = () => {
    if (sectors.length === 0) {
      return (
        <div className="no-sectors">
          <p>No sectors found. Generate a galaxy first!</p>
        </div>
      );
    }

    const mapWidth = 800;
    const mapHeight = 600;

    // Calculate bounds from sector coordinates
    const xCoords = sectors.map((s: any) => s.x_coord);
    const yCoords = sectors.map((s: any) => s.y_coord);
    const minX = Math.min(...xCoords);
    const maxX = Math.max(...xCoords);
    const minY = Math.min(...yCoords);
    const maxY = Math.max(...yCoords);

    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;
    const padding = 40;

    // Map sector coords to SVG coords
    const toSvgX = (x: number) => padding + ((x - minX) / rangeX) * (mapWidth - 2 * padding);
    const toSvgY = (y: number) => padding + ((y - minY) / rangeY) * (mapHeight - 2 * padding);

    // Get color based on sector type
    const getSectorColor = (type: string) => {
      switch (type?.toUpperCase()) {
        case 'VOID': return '#6b7280';
        case 'NEBULA': return '#8b5cf6';
        case 'STANDARD': return '#3b82f6';
        default: return '#3b82f6';
      }
    };

    return (
      <div className="galaxy-map-container" ref={mapContainerRef}>
        <div className="galaxy-map-controls">
          <button onClick={() => setMapZoom(prev => Math.min(10, prev * 1.3))} className="map-control-btn">+</button>
          <button onClick={() => setMapZoom(prev => Math.max(0.1, prev * 0.7))} className="map-control-btn">-</button>
          <button onClick={() => { setMapZoom(1); setMapOffset({ x: 0, y: 0 }); }} className="map-control-btn">Reset</button>
          <span className="map-info">{sectors.length} sectors | Zoom: {mapZoom.toFixed(1)}x</span>
        </div>
        <div className="galaxy-map-legend">
          <span className="legend-item"><span className="legend-dot" style={{ background: '#3b82f6' }}></span> Standard</span>
          <span className="legend-item"><span className="legend-dot" style={{ background: '#6b7280' }}></span> Void</span>
          <span className="legend-item"><span className="legend-dot" style={{ background: '#8b5cf6' }}></span> Nebula</span>
          <span className="legend-item"><span className="legend-ring" style={{ borderColor: '#22c55e' }}></span> Planet</span>
          <span className="legend-item"><span className="legend-ring" style={{ borderColor: '#eab308' }}></span> Port</span>
        </div>
        <div
          className="galaxy-map-viewport"
          onWheel={handleMapWheel}
          onMouseDown={handleMapMouseDown}
          onMouseMove={handleMapMouseMove}
          onMouseUp={handleMapMouseUp}
          onMouseLeave={handleMapMouseUp}
          style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
        >
          <svg
            width={mapWidth}
            height={mapHeight}
            viewBox={`0 0 ${mapWidth} ${mapHeight}`}
            style={{
              transform: `translate(${mapOffset.x}px, ${mapOffset.y}px) scale(${mapZoom})`,
              transformOrigin: 'center center'
            }}
          >
            {/* Background */}
            <rect x="0" y="0" width={mapWidth} height={mapHeight} fill="#0a0a1a" rx="4" />

            {/* Grid lines */}
            {Array.from({ length: 11 }, (_, i) => {
              const x = padding + (i / 10) * (mapWidth - 2 * padding);
              const y = padding + (i / 10) * (mapHeight - 2 * padding);
              return (
                <g key={`grid-${i}`}>
                  <line x1={x} y1={padding} x2={x} y2={mapHeight - padding} stroke="#1a1a3a" strokeWidth="0.5" />
                  <line x1={padding} y1={y} x2={mapWidth - padding} y2={y} stroke="#1a1a3a" strokeWidth="0.5" />
                </g>
              );
            })}

            {/* Sector dots */}
            {sectors.map((sector: any) => {
              const cx = toSvgX(sector.x_coord);
              const cy = toSvgY(sector.y_coord);
              const color = getSectorColor(sector.type);
              const dotRadius = 3 / Math.max(1, mapZoom * 0.5);

              return (
                <g
                  key={sector.id}
                  onMouseEnter={(e) => {
                    setHoveredSector(sector);
                    const rect = mapContainerRef.current?.getBoundingClientRect();
                    if (rect) {
                      setTooltipPos({ x: e.clientX - rect.left + 10, y: e.clientY - rect.top - 10 });
                    }
                  }}
                  onMouseLeave={() => setHoveredSector(null)}
                  onClick={() => handleSectorClick(sector)}
                  style={{ cursor: 'pointer' }}
                >
                  {/* Planet ring */}
                  {sector.has_planet && (
                    <circle cx={cx} cy={cy} r={dotRadius + 3} fill="none" stroke="#22c55e" strokeWidth="1" opacity="0.7" />
                  )}
                  {/* Port ring */}
                  {sector.has_port && (
                    <circle cx={cx} cy={cy} r={dotRadius + 5} fill="none" stroke="#eab308" strokeWidth="1" opacity="0.7" />
                  )}
                  {/* Sector dot */}
                  <circle cx={cx} cy={cy} r={dotRadius} fill={color} opacity="0.85" />
                </g>
              );
            })}
          </svg>
        </div>

        {/* Tooltip */}
        {hoveredSector && (
          <div
            className="galaxy-map-tooltip"
            style={{ left: tooltipPos.x, top: tooltipPos.y }}
          >
            <div className="tooltip-title">Sector {hoveredSector.sector_id}</div>
            <div className="tooltip-name">{hoveredSector.name}</div>
            <div className="tooltip-type">Type: {hoveredSector.type}</div>
            <div className="tooltip-coords">Coords: ({hoveredSector.x_coord}, {hoveredSector.y_coord}, {hoveredSector.z_coord})</div>
            <div className="tooltip-hazard">Hazard: {hoveredSector.hazard_level?.toFixed(1)}</div>
            {hoveredSector.has_port && <div className="tooltip-feature">Has Port</div>}
            {hoveredSector.has_planet && <div className="tooltip-feature">Has Planet</div>}
            {hoveredSector.has_warp_tunnel && <div className="tooltip-feature">Has Warp Tunnel</div>}
          </div>
        )}
      </div>
    );
  };

  // Render sectors grid
  const renderSectorsGrid = () => {
    console.log('Rendering sectors grid, sectors:', sectors);
    
    return (
      <div className="sectors-grid-container">
        {sectors.length === 0 ? (
          <div className="no-sectors">
            <p>No sectors found. Generate a galaxy first!</p>
          </div>
        ) : (
          <div className="sectors-grid">
            {sectors.map(sector => (
              <div 
                key={sector.id} 
                className={`sector-card ${sector.has_port ? 'has-port' : ''} ${sector.has_planet ? 'has-planet' : ''}`}
                onClick={() => handleSectorClick(sector)}
              >
                <div className="sector-header">
                  <h4>Sector {sector.sector_id}</h4>
                  <span className={`sector-type ${sector.type.toLowerCase()}`}>{sector.type}</span>
                </div>
                <p className="sector-name">{sector.name}</p>
                <div className="sector-info">
                  <span className="hazard-level">Hazard: {sector.hazard_level.toFixed(1)}</span>
                  <span className="coordinates">({sector.x_coord}, {sector.y_coord}, {sector.z_coord})</span>
                </div>
                <div className="sector-features">
                  {sector.has_port && <span className="feature-badge port">🏪 Port</span>}
                  {sector.has_planet && <span className="feature-badge planet">🌍 Planet</span>}
                  {sector.has_warp_tunnel && <span className="feature-badge warp">🌀 Warp</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  // Main render based on view state
  const renderContent = () => {
    switch (viewState.type) {
      case 'sector':
        return (
          <SectorDetail 
            sector={viewState.data} 
            onBack={handleBack}
            onPortClick={handlePortClick}
            onPlanetClick={handlePlanetClick}
          />
        );
      case 'port':
        return (
          <PortDetail 
            port={viewState.data} 
            onBack={handleBack}
          />
        );
      case 'planet':
        return (
          <PlanetDetail 
            planet={viewState.data} 
            onBack={handleBack}
          />
        );
      default:
        return (
          <>
            {false ? (
              // Inline generator branch removed in bang cutover (Phase 4A); see /universe/bang.
              null
            ) : (
              <>
                <div className="universe-tabs">
                  <button 
                    className={`tab ${activeTab === 'galaxy' ? 'active' : ''}`}
                    onClick={() => setActiveTab('galaxy')}
                  >
                    🌌 Galaxy Overview
                  </button>
                  <button 
                    className={`tab ${activeTab === 'sectors' ? 'active' : ''}`}
                    onClick={() => setActiveTab('sectors')}
                  >
                    🔲 Sectors
                  </button>
                  <button 
                    className={`tab ${activeTab === 'map' ? 'active' : ''}`}
                    onClick={() => setActiveTab('map')}
                  >
                    🗺️ Galaxy Map
                  </button>
                </div>

                <div className="universe-content">
                  {activeTab === 'galaxy' && renderGalaxyOverview()}
                  {activeTab === 'sectors' && renderSectorsGrid()}
                  {activeTab === 'map' && renderGalaxyMap()}
                </div>
              </>
            )}
          </>
        );
    }
  };

  return (
    <div className="universe-manager">
      <div className="starfield-background"></div>
      <div className="universe-container">
        {error && (
          <div className="error-message">
            {error}
          </div>
        )}
        {isLoading && (
          <div className="loading-overlay">
            <div className="loading-spinner"></div>
            <p>Loading universe data...</p>
          </div>
        )}
        {renderContent()}
      </div>
    </div>
  );
};

export default UniverseManager;