import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useAdmin, SectorData } from '../../contexts/AdminContext';
import * as d3 from 'd3';

const Universe: React.FC = () => {
  const { 
    galaxyState, 
    zones, 
    clusters,
    sectors,
    loadGalaxyInfo,
    loadZones,
    loadClusters,
    loadSectors,
    addSectors,
    createWarpTunnel,
    clearGalaxyData,
    isLoading,
    error
  } = useAdmin();

  // UI State
  const [activeTab, setActiveTab] = useState<'overview' | 'visualization' | 'management'>('overview');
  const [selectedRegion, setSelectedRegion] = useState<string | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const [selectedSector, setSelectedSector] = useState<SectorData | null>(null);
  // showGenerateForm state removed in bang cutover (Phase 4A); generation lives at /universe/bang
  const [showAddSectorsForm, setShowAddSectorsForm] = useState(false);
  const [showWarpTunnelForm, setShowWarpTunnelForm] = useState(false);

  // Galaxy generation form state
  const [newGalaxyName, setNewGalaxyName] = useState('');
  const [newGalaxySectors, setNewGalaxySectors] = useState(100); // Changed from 500 to 100
  const [resourceDistribution, setResourceDistribution] = useState<'balanced' | 'clustered' | 'random'>('balanced');
  const [hazardLevels, setHazardLevels] = useState<'low' | 'moderate' | 'high' | 'extreme'>('moderate');
  const [connectivity, setConnectivity] = useState<'sparse' | 'normal' | 'dense'>('normal');
  const [portDensity, setPortDensity] = useState<number>(0.15);
  const [planetDensity, setPlanetDensity] = useState<number>(0.25);
  const [warpTunnelProbability] = useState<number>(0.1);
  const [factionTerritorySize] = useState<number>(25);
  
  // Region distribution state (percentages that must total 100%)
  const [regionDistribution, setRegionDistribution] = useState({
    federation: 40,
    border: 35,
    frontier: 25
  });

  // Add sectors form state
  const [addSectorsCount, setAddSectorsCount] = useState(10);
  const [sectorType, setSectorType] = useState<'normal' | 'nebula' | 'black_hole' | 'asteroid_field'>('normal');
  const [resourceRichness, setResourceRichness] = useState<'poor' | 'average' | 'rich' | 'abundant'>('average');

  // Warp tunnel form state
  const [sourceSectorId, setSourceSectorId] = useState<number | null>(null);
  const [targetSectorId, setTargetSectorId] = useState<number | null>(null);
  const [tunnelStability, setTunnelStability] = useState<number>(0.75);

  // Port stock update state
  const [isUpdatingPortStock, setIsUpdatingPortStock] = useState(false);
  const [_portStockUpdateResult, setPortStockUpdateResult] = useState<any>(null);

  // D3 visualization ref
  const svgRef = useRef<SVGSVGElement | null>(null);

  // Load data on mount
  useEffect(() => {
    loadGalaxyInfo();
  }, []);

  useEffect(() => {
    if (galaxyState) {
      loadZones();
      loadSectors();
    }
  }, [galaxyState]);

  useEffect(() => {
    if (selectedRegion) {
      loadClusters(selectedRegion);
    }
  }, [selectedRegion]);

  // Handle region distribution slider changes with automatic adjustment
  const handleRegionDistributionChange = (regionType: 'federation' | 'border' | 'frontier', newValue: number) => {
    const currentValue = regionDistribution[regionType];
    const difference = newValue - currentValue;
    
    // Get the other two regions
    const otherRegions = Object.keys(regionDistribution).filter(key => key !== regionType) as ('federation' | 'border' | 'frontier')[];
    
    // Calculate how much to subtract from each other region
    let remaining = difference;
    const newDistribution = { ...regionDistribution };
    newDistribution[regionType] = newValue;
    
    // Distribute the difference proportionally among the other regions
    const otherRegionsTotal = otherRegions.reduce((sum, region) => sum + regionDistribution[region], 0);
    
    if (otherRegionsTotal > 0) {
      for (const otherRegion of otherRegions) {
        const proportion = regionDistribution[otherRegion] / otherRegionsTotal;
        const adjustment = remaining * proportion;
        newDistribution[otherRegion] = Math.max(0, Math.min(100, regionDistribution[otherRegion] - adjustment));
      }
    }
    
    // Ensure the total is exactly 100%
    const total = newDistribution.federation + newDistribution.border + newDistribution.frontier;
    if (total !== 100) {
      // Adjust the first other region to make total exactly 100
      const adjustmentNeeded = 100 - total;
      newDistribution[otherRegions[0]] = Math.max(0, Math.min(100, newDistribution[otherRegions[0]] + adjustmentNeeded));
    }
    
    setRegionDistribution(newDistribution);
  };

  // Legacy handleGenerateGalaxy removed in bang cutover (Phase 4A).
  // Generation now lives at /universe/bang (BangGalaxyPage).

  // Handle adding sectors
  const handleAddSectors = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!galaxyState) return;
    
    const sectorConfig = {
      region_id: selectedRegion || undefined,
      cluster_id: selectedCluster || undefined,
      sector_type: sectorType,
      resource_richness: resourceRichness
    };
    
    try {
      await addSectors(galaxyState.id, addSectorsCount, sectorConfig);
      setShowAddSectorsForm(false);
      await loadGalaxyInfo();
      await loadZones();
      await loadSectors();
    } catch (error) {
      console.error('Error adding sectors:', error);
      alert('Failed to add sectors. Please try again.');
    }
  };

  // Handle creating a warp tunnel
  const handleCreateWarpTunnel = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!sourceSectorId || !targetSectorId) {
      alert('Please specify both source and target sectors.');
      return;
    }
    
    try {
      await createWarpTunnel(sourceSectorId, targetSectorId, tunnelStability);
      setShowWarpTunnelForm(false);
      await loadGalaxyInfo();
      await loadSectors();
    } catch (error) {
      console.error('Error creating warp tunnel:', error);
      alert('Failed to create warp tunnel. Please try again.');
    }
  };

  // Handle updating port stock levels
  const handleUpdatePortStock = async () => {
    if (!confirm('This will update stock levels for all ports in the universe to match their trading roles. Continue?')) {
      return;
    }

    try {
      setIsUpdatingPortStock(true);
      setPortStockUpdateResult(null);
      
      const response = await fetch('/api/v1/admin/ports/update-stock-levels', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`,
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        throw new Error(`Failed to update port stock: ${response.statusText}`);
      }

      const result = await response.json();
      setPortStockUpdateResult(result);
      
      alert(`Successfully updated stock levels for ${result.ports_updated} ports out of ${result.total_ports} total!`);
      
    } catch (error) {
      console.error('Error updating port stock:', error);
      alert('Failed to update port stock levels. Please try again.');
    } finally {
      setIsUpdatingPortStock(false);
    }
  };

  // Convert sector data for D3 visualization
  const convertSectorDataToMapData = (sectorsData: SectorData[]) => {
    const nodes = sectorsData.map(sector => ({
      id: sector.sector_id,
      name: sector.name,
      type: sector.type.toLowerCase(),
      x: sector.x_coord * 10 + 500,
      y: sector.y_coord * 10 + 400,
      region_id: sector.cluster_id,
      cluster_id: sector.cluster_id,
      hazard_level: sector.hazard_level,
      has_port: sector.has_port,
      has_planet: sector.has_planet,
      has_warp_tunnel: sector.has_warp_tunnel,
      resource_richness: sector.resource_richness,
      controlling_faction: sector.controlling_faction,
      is_discovered: sector.is_discovered
    }));

    // Create links based on proximity and warp tunnels
    const links: any[] = [];
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const distance = Math.sqrt(
          Math.pow(nodes[i].x - nodes[j].x, 2) + 
          Math.pow(nodes[i].y - nodes[j].y, 2)
        );
        
        if (distance < 80) {
          links.push({
            source: nodes[i].id,
            target: nodes[j].id,
            is_warp_tunnel: nodes[i].has_warp_tunnel && nodes[j].has_warp_tunnel && Math.random() > 0.7
          });
        }
      }
    }

    return { nodes, links };
  };

  // D3 Visualization
  useEffect(() => {
    if (!svgRef.current || !sectors || sectors.length === 0 || activeTab !== 'visualization') return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const width = 1200;
    const height = 800;
    const mapData = convertSectorDataToMapData(sectors);

    // Set up zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 5])
      .on('zoom', (event) => {
        container.attr('transform', event.transform);
      });

    svg.call(zoom);

    // Container for all elements
    const container = svg.append('g');

    // Define gradients for special sectors
    const defs = svg.append('defs');
    
    const nebulaGradient = defs.append('radialGradient')
      .attr('id', 'nebula-gradient');
    nebulaGradient.append('stop')
      .attr('offset', '0%')
      .attr('stop-color', '#e74c3c')
      .attr('stop-opacity', 0.8);
    nebulaGradient.append('stop')
      .attr('offset', '100%')
      .attr('stop-color', '#c0392b')
      .attr('stop-opacity', 0.3);

    // Create links
    container.append('g')
      .attr('class', 'links')
      .selectAll('line')
      .data(mapData.links)
      .enter()
      .append('line')
      .attr('class', d => d.is_warp_tunnel ? 'warp-tunnel' : 'normal-link')
      .attr('stroke', d => d.is_warp_tunnel ? '#9b59b6' : '#34495e')
      .attr('stroke-width', d => d.is_warp_tunnel ? 3 : 1)
      .attr('stroke-dasharray', d => d.is_warp_tunnel ? '5,5' : null)
      .attr('opacity', d => d.is_warp_tunnel ? 0.8 : 0.3)
      .attr('x1', d => mapData.nodes.find(n => n.id === d.source)?.x || 0)
      .attr('y1', d => mapData.nodes.find(n => n.id === d.source)?.y || 0)
      .attr('x2', d => mapData.nodes.find(n => n.id === d.target)?.x || 0)
      .attr('y2', d => mapData.nodes.find(n => n.id === d.target)?.y || 0);

    // Create sector groups
    const node = container.append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(mapData.nodes)
      .enter()
      .append('g')
      .attr('class', 'sector-node')
      .attr('transform', d => `translate(${d.x}, ${d.y})`)
      .style('cursor', 'pointer')
      .on('click', (_event, d) => {
        const sectorData = sectors.find(s => s.sector_id === d.id);
        if (sectorData) {
          setSelectedSector(sectorData);
        }
      });

    // Main sector circle
    node.append('circle')
      .attr('r', d => {
        if (d.type === 'black_hole') return 12;
        if (d.type === 'nebula') return 10;
        return 8;
      })
      .attr('fill', d => {
        const colorMap: { [key: string]: string } = {
          normal: d.is_discovered ? '#3498db' : '#7f8c8d',
          nebula: 'url(#nebula-gradient)',
          asteroid_field: '#f39c12',
          black_hole: '#2c3e50'
        };
        return colorMap[d.type] || '#3498db';
      })
      .attr('stroke', d => {
        if (d.controlling_faction) return '#e74c3c';
        return '#2c3e50';
      })
      .attr('stroke-width', d => d.controlling_faction ? 3 : 2)
      .attr('opacity', d => d.is_discovered ? 1 : 0.5);

    // Port indicator
    node.filter(d => d.has_port)
      .append('circle')
      .attr('r', 3)
      .attr('cx', 10)
      .attr('cy', -10)
      .attr('fill', '#2ecc71')
      .attr('stroke', '#27ae60')
      .attr('stroke-width', 1);

    // Planet indicator
    node.filter(d => d.has_planet)
      .append('circle')
      .attr('r', 3)
      .attr('cx', -10)
      .attr('cy', -10)
      .attr('fill', '#e67e22')
      .attr('stroke', '#d35400')
      .attr('stroke-width', 1);

    // Hazard indicator for high hazard sectors
    node.filter(d => d.hazard_level > 7)
      .append('text')
      .attr('x', 0)
      .attr('y', -15)
      .attr('text-anchor', 'middle')
      .attr('fill', '#e74c3c')
      .attr('font-size', '16px')
      .text('⚠');

    // Sector ID label
    node.append('text')
      .attr('dy', 20)
      .attr('text-anchor', 'middle')
      .attr('class', 'sector-label')
      .attr('fill', '#2c3e50')
      .attr('font-size', '10px')
      .attr('font-weight', 'bold')
      .text(d => `S${d.id}`);

    // Tooltip
    const tooltip = d3.select('body').append('div')
      .attr('class', 'universe-tooltip')
      .style('opacity', 0)
      .style('position', 'absolute')
      .style('background', 'rgba(0, 0, 0, 0.9)')
      .style('color', 'white')
      .style('padding', '10px')
      .style('border-radius', '5px')
      .style('pointer-events', 'none');

    node.on('mouseover', (event, d) => {
      tooltip.transition()
        .duration(200)
        .style('opacity', .95);
      
      const factionName = d.controlling_faction || 'Unclaimed';
      tooltip.html(`
        <strong>${d.name}</strong><br/>
        <span style="color: #7f8c8d">Sector ${d.id}</span><br/>
        <hr style="margin: 5px 0; border-color: #34495e"/>
        Type: ${d.type}<br/>
        Hazard: <span style="color: ${d.hazard_level > 7 ? '#e74c3c' : d.hazard_level > 4 ? '#f39c12' : '#2ecc71'}">${d.hazard_level.toFixed(1)}</span><br/>
        Resources: ${d.resource_richness}<br/>
        Faction: <span style="color: #e74c3c">${factionName}</span><br/>
        ${d.has_port ? '<span style="color: #2ecc71">✓ Port</span><br/>' : ''}
        ${d.has_planet ? '<span style="color: #e67e22">✓ Planet</span><br/>' : ''}
        ${d.has_warp_tunnel ? '<span style="color: #9b59b6">✓ Warp Tunnel</span>' : ''}
      `)
        .style('left', (event.pageX + 10) + 'px')
        .style('top', (event.pageY - 28) + 'px');
    })
    .on('mouseout', () => {
      tooltip.transition()
        .duration(500)
        .style('opacity', 0);
    });

    // Initial zoom to fit
    const bounds = container.node()?.getBBox();
    if (bounds) {
      const fullWidth = bounds.width;
      const fullHeight = bounds.height;
      const midX = bounds.x + fullWidth / 2;
      const midY = bounds.y + fullHeight / 2;
      
      const scale = 0.8 / Math.max(fullWidth / width, fullHeight / height);
      const translate = [width / 2 - scale * midX, height / 2 - scale * midY];
      
      svg.call(
        zoom.transform,
        d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale)
      );
    }

    return () => {
      d3.select('body').selectAll('.universe-tooltip').remove();
    };
  }, [sectors, activeTab]);

  if (isLoading) {
    return (
      <div className="page-container">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading universe data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-container">
        <div className="alert alert-error">
          <h2>Error Loading Universe</h2>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={() => loadGalaxyInfo()}>Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <div className="page-header">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="page-title">Universe Administration</h1>
            <p className="page-subtitle">Manage galaxy generation, sectors, and universe structure</p>
          </div>
          {!galaxyState && (
            <Link className="btn btn-primary" to="/universe/bang">
              Generate New Galaxy
            </Link>
          )}
        </div>
      </div>
      <div className="page-content">

        {!galaxyState ? (
          <div className="empty-state">
            <h2>No Galaxy Found</h2>
            <p>Generate a new galaxy to begin universe administration.</p>
          </div>
        ) : (
          <>
            <div className="tabs">
              <button 
                className={`tab ${activeTab === 'overview' ? 'tab-active' : ''}`}
                onClick={() => setActiveTab('overview')}
              >
                Overview
              </button>
              <button 
                className={`tab ${activeTab === 'visualization' ? 'tab-active' : ''}`}
                onClick={() => setActiveTab('visualization')}
              >
                Galaxy Map
              </button>
              <button 
                className={`tab ${activeTab === 'management' ? 'tab-active' : ''}`}
                onClick={() => setActiveTab('management')}
              >
                Management
              </button>
            </div>

            <div className="tab-content">
              {activeTab === 'overview' && (
                <div className="space-y-6">
                  <section className="section">
                    <div className="section-header">
                      <h2 className="section-title">{galaxyState.name}</h2>
                      <p className="section-subtitle">Age: {galaxyState.state.age_in_days} days</p>
                    </div>
                  </section>

                  <section className="section">
                    <h3 className="section-title mb-4">Galaxy Statistics</h3>
                    <div className="grid grid-auto-fit gap-6">
                      <div className="dashboard-stat-card">
                        <div className="dashboard-stat-header">
                          <span className="dashboard-stat-icon">🌌</span>
                          <h4 className="dashboard-stat-title">Sectors</h4>
                        </div>
                        <div className="dashboard-stat-value">{galaxyState.statistics.total_sectors}</div>
                        <div className="dashboard-stat-detail">
                          {galaxyState.statistics.discovered_sectors} discovered
                          ({galaxyState.state.exploration_percentage.toFixed(1)}%)
                        </div>
                      </div>

                      <div className="dashboard-stat-card">
                        <div className="dashboard-stat-header">
                          <span className="dashboard-stat-icon">🗺️</span>
                          <h4 className="dashboard-stat-title">Regions</h4>
                        </div>
                        <div className="dashboard-stat-value">{zones.length}</div>
                        <div className="dashboard-stat-detail">
                          Federation: {galaxyState.zone_distribution.federation}%<br/>
                          Border: {galaxyState.zone_distribution.border}%<br/>
                          Frontier: {galaxyState.zone_distribution.frontier}%
                        </div>
                      </div>

                      <div className="dashboard-stat-card">
                        <div className="dashboard-stat-header">
                          <span className="dashboard-stat-icon">🏗️</span>
                          <h4 className="dashboard-stat-title">Infrastructure</h4>
                        </div>
                        <div className="dashboard-stat-value">
                          {galaxyState.statistics.station_count + galaxyState.statistics.planet_count}
                        </div>
                        <div className="dashboard-stat-detail">
                          Ports: {galaxyState.statistics.station_count}<br/>
                          Planets: {galaxyState.statistics.planet_count}<br/>
                          Warp Tunnels: {galaxyState.statistics.warp_tunnel_count}
                        </div>
                      </div>

                      <div className="dashboard-stat-card">
                        <div className="dashboard-stat-header">
                          <span className="dashboard-stat-icon">👥</span>
                          <h4 className="dashboard-stat-title">Activity</h4>
                        </div>
                        <div className="dashboard-stat-value">{galaxyState.statistics.player_count}</div>
                        <div className="dashboard-stat-detail">
                          Active Players<br/>
                          Teams: {galaxyState.statistics.team_count}
                        </div>
                      </div>

                      <div className="dashboard-stat-card">
                        <div className="dashboard-stat-header">
                          <span className="dashboard-stat-icon">💰</span>
                          <h4 className="dashboard-stat-title">Economy</h4>
                        </div>
                        <div className="dashboard-stat-value">{galaxyState.state.economic_health}%</div>
                        <div className="dashboard-stat-detail">
                          Economic Health<br/>
                          Health Score: {galaxyState.state.economic_health.toFixed(1)}
                        </div>
                      </div>

                      <div className="dashboard-stat-card">
                        <div className="dashboard-stat-header">
                          <span className="dashboard-stat-icon">⚙️</span>
                          <h4 className="dashboard-stat-title">Settings</h4>
                        </div>
                        <div className="dashboard-stat-detail">
                          Age: {galaxyState.state.age_in_days} days<br/>
                          Exploration: {galaxyState.state.exploration_percentage.toFixed(1)}%
                        </div>
                      </div>
                    </div>
                  </section>

                  <section className="section">
                    <h3 className="section-title mb-4">Faction Influence</h3>
                    <div className="card">
                      <div className="card-body">
                        <div className="space-y-4">
                          {Object.entries(galaxyState.zone_distribution).map(([region, count]) => (
                            <div key={region} className="flex justify-between items-center">
                              <div className="font-medium text-primary">{region.replace(/_/g, ' ').toUpperCase()}</div>
                              <div className="flex items-center gap-4 flex-1 max-w-md">
                                <div className="flex-1 bg-surface-secondary rounded-full h-2">
                                  <div 
                                    className="bg-primary h-2 rounded-full transition-all duration-300" 
                                    style={{ width: `${(count / (galaxyState.zone_distribution.federation + galaxyState.zone_distribution.border + galaxyState.zone_distribution.frontier)) * 100}%` }}
                                  />
                                </div>
                                <span className="text-sm font-semibold text-muted min-w-8">{count}%</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </section>
                </div>
              )}

            {activeTab === 'visualization' && (
              <div className="visualization-tab">
                <div className="visualization-controls">
                  <select 
                    value={selectedRegion || ''} 
                    onChange={(e) => setSelectedRegion(e.target.value || null)}
                  >
                    <option value="">All Regions</option>
                    {zones.map(region => (
                      <option key={region.id} value={region.id}>
                        {region.name} ({region.sector_count} sectors)
                      </option>
                    ))}
                  </select>

                  <select 
                    value={selectedCluster || ''} 
                    onChange={(e) => setSelectedCluster(e.target.value || null)}
                    disabled={!selectedRegion}
                  >
                    <option value="">All Clusters</option>
                    {clusters
                      .filter(cluster => !selectedRegion || cluster.region_id === selectedRegion)
                      .map(cluster => (
                        <option key={cluster.id} value={cluster.id}>
                          {cluster.name} ({cluster.sector_count} sectors)
                        </option>
                      ))}
                  </select>

                  {selectedSector && (
                    <div className="selected-sector-info">
                      <h4>{selectedSector.name}</h4>
                      <p>Sector {selectedSector.sector_id} • {selectedSector.type}</p>
                      <button 
                        onClick={() => {
                          setSourceSectorId(selectedSector.sector_id);
                          setShowWarpTunnelForm(true);
                        }}
                      >
                        Create Warp From Here
                      </button>
                    </div>
                  )}
                </div>

                <div className="map-container">
                  <svg 
                    ref={svgRef} 
                    width="1200" 
                    height="800"
                    className="universe-map"
                  />
                </div>

                <div className="map-legend">
                  <h4>Legend</h4>
                  <div className="legend-grid">
                    <div className="legend-item">
                      <span className="legend-color normal"></span> Normal Sector
                    </div>
                    <div className="legend-item">
                      <span className="legend-color nebula"></span> Nebula
                    </div>
                    <div className="legend-item">
                      <span className="legend-color asteroid"></span> Asteroid Field
                    </div>
                    <div className="legend-item">
                      <span className="legend-color black-hole"></span> Black Hole
                    </div>
                    <div className="legend-item">
                      <span className="legend-indicator port"></span> Port
                    </div>
                    <div className="legend-item">
                      <span className="legend-indicator planet"></span> Planet
                    </div>
                    <div className="legend-item">
                      <span className="legend-line warp"></span> Warp Tunnel
                    </div>
                    <div className="legend-item">
                      <span className="legend-color undiscovered"></span> Undiscovered
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'management' && (
              <div className="management-tab">
                <div className="management-actions">
                  <button 
                    className="btn btn-primary"
                    onClick={() => setShowAddSectorsForm(true)}
                  >
                    Add Sectors
                  </button>
                  <button 
                    className="btn btn-secondary"
                    onClick={() => setShowWarpTunnelForm(true)}
                  >
                    Create Warp Tunnel
                  </button>
                  <button 
                    className="btn btn-info"
                    onClick={handleUpdatePortStock}
                    disabled={isUpdatingPortStock}
                  >
                    {isUpdatingPortStock ? 'Updating...' : 'Update Port Stock Levels'}
                  </button>
                  <Link to="/universe/bang" className="btn btn-danger">
                    Regenerate Galaxy
                  </Link>
                </div>

                <div className="regions-list">
                  <h3>Regions & Clusters</h3>
                  {zones.map(region => (
                    <div key={region.id} className="region-item">
                      <div className="region-header">
                        <h4>{region.name}</h4>
                        <span>{region.sector_count} sectors</span>
                      </div>
                      <div className="clusters-list">
                        {clusters
                          .filter(cluster => cluster.region_id === region.id)
                          .map(cluster => (
                            <div key={cluster.id} className="cluster-item">
                              <span>{cluster.name}</span>
                              <span className="cluster-type">{cluster.type}</span>
                              <span>{cluster.sector_count} sectors</span>
                            </div>
                          ))}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="recent-sectors">
                  <h3>Recent Sectors</h3>
                  <div className="sectors-table">
                    <table>
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Name</th>
                          <th>Type</th>
                          <th>Coordinates</th>
                          <th>Features</th>
                          <th>Hazard</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sectors.slice(0, 20).map(sector => (
                          <tr key={sector.id}>
                            <td>{sector.sector_id}</td>
                            <td>{sector.name}</td>
                            <td className={`sector-type ${sector.type}`}>{sector.type}</td>
                            <td>{sector.x_coord}, {sector.y_coord}, {sector.z_coord}</td>
                            <td>
                              {sector.has_port && <span className="feature-badge port">Port</span>}
                              {sector.has_planet && <span className="feature-badge planet">Planet</span>}
                              {sector.has_warp_tunnel && <span className="feature-badge warp">Warp</span>}
                            </td>
                            <td className={`hazard-level ${sector.hazard_level > 7 ? 'high' : sector.hazard_level > 4 ? 'medium' : 'low'}`}>
                              {sector.hazard_level.toFixed(1)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}
          </div>
          </>
        )}


      {/* Add Sectors Modal */}
      {showAddSectorsForm && (
        <div className="modal-overlay" onClick={() => setShowAddSectorsForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Add Sectors</h2>
            <form onSubmit={handleAddSectors}>
              <div className="form-group">
                <label>Number of Sectors</label>
                <input 
                  type="number" 
                  value={addSectorsCount}
                  onChange={(e) => setAddSectorsCount(parseInt(e.target.value))}
                  min="1"
                  max="100"
                  required
                />
              </div>

              <div className="form-group">
                <label>Sector Type</label>
                <select value={sectorType} onChange={(e) => setSectorType(e.target.value as any)}>
                  <option value="normal">Normal</option>
                  <option value="nebula">Nebula</option>
                  <option value="black_hole">Black Hole</option>
                  <option value="asteroid_field">Asteroid Field</option>
                </select>
              </div>

              <div className="form-group">
                <label>Resource Richness</label>
                <select value={resourceRichness} onChange={(e) => setResourceRichness(e.target.value as any)}>
                  <option value="poor">Poor</option>
                  <option value="average">Average</option>
                  <option value="rich">Rich</option>
                  <option value="abundant">Abundant</option>
                </select>
              </div>

              <div className="form-actions">
                <button type="button" className="btn btn-secondary" onClick={() => setShowAddSectorsForm(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary">
                  Add Sectors
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Create Warp Tunnel Modal */}
      {showWarpTunnelForm && (
        <div className="modal-overlay" onClick={() => setShowWarpTunnelForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Create Warp Tunnel</h2>
            <form onSubmit={handleCreateWarpTunnel}>
              <div className="form-group">
                <label>Source Sector ID</label>
                <input 
                  type="number" 
                  value={sourceSectorId || ''}
                  onChange={(e) => setSourceSectorId(parseInt(e.target.value))}
                  placeholder="Enter source sector ID"
                  required
                />
              </div>

              <div className="form-group">
                <label>Target Sector ID</label>
                <input 
                  type="number" 
                  value={targetSectorId || ''}
                  onChange={(e) => setTargetSectorId(parseInt(e.target.value))}
                  placeholder="Enter target sector ID"
                  required
                />
              </div>

              <div className="form-group">
                <label>Tunnel Stability ({(tunnelStability * 100).toFixed(0)}%)</label>
                <input 
                  type="range" 
                  min="0.1" 
                  max="1" 
                  step="0.05"
                  value={tunnelStability}
                  onChange={(e) => setTunnelStability(parseFloat(e.target.value))}
                />
              </div>

              <div className="form-actions">
                <button type="button" className="btn btn-secondary" onClick={() => setShowWarpTunnelForm(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary">
                  Create Warp Tunnel
                </button>
              </div>
            </form>
          </div>
        </div>
        )}
      </div>
    </div>
  );
};

export default Universe;