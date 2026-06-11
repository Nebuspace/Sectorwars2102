import React, { useState, useEffect } from 'react';
import { useAdmin } from '../../contexts/AdminContext';
import { api } from '../../utils/auth';
import SectorEditModal from '../universe/SectorEditModal';

interface Sector {
  id: string;
  sector_id: number;
  name: string;
  type: string;
  cluster_id: string;
  cluster_name?: string | null;
  region_name?: string | null;
  x_coord: number;
  y_coord: number;
  z_coord: number;
  hazard_level: number;
  is_discovered: boolean;
  has_port: boolean;
  has_planet: boolean;
  has_warp_tunnel: boolean;
  player_count: number;
  controlling_faction: string | null;
}

const SectorsManager: React.FC = () => {
  const {
    galaxyState,
    regions,
    clusters,
    loadGalaxyInfo,
    loadRegions,
    loadClusters,
    isLoading,
    error
  } = useAdmin();
  
  // State for sectors
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [totalSectors, setTotalSectors] = useState<number>(0);
  const [selectedSector, setSelectedSector] = useState<Sector | null>(null);
  const [sectorLoading, setSectorLoading] = useState<boolean>(false);
  
  // Modal state
  const [editingSector, setEditingSector] = useState<Sector | null>(null);
  const [isEditModalOpen, setIsEditModalOpen] = useState<boolean>(false);
  
  // Filters
  const [selectedRegion, setSelectedRegion] = useState<string>('');
  const [selectedCluster, setSelectedCluster] = useState<string>('');
  const [filterHasPort, setFilterHasPort] = useState<boolean | null>(null);
  const [filterHasPlanet, setFilterHasPlanet] = useState<boolean | null>(null);
  const [filterDiscovered, setFilterDiscovered] = useState<boolean | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  
  // Pagination - Ultra-optimized for 1,000+ sectors  
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [itemsPerPage] = useState<number>(100); // Increased to 100 for maximum efficiency
  
  // Load galaxy info on component mount
  useEffect(() => {
    loadGalaxyInfo();
  }, []);
  
  // Load regions when galaxy info is loaded
  useEffect(() => {
    if (galaxyState) {
      loadRegions();
    }
  }, [galaxyState]);
  
  // Load sectors based on filters
  useEffect(() => {
    const fetchSectors = async () => {
      if (!galaxyState) return;
      
      setSectorLoading(true);
      
      try {
        // Use authenticated API with query parameters
        const response = await api.get('/api/v1/admin/sectors', {
          params: {
            filter_region: selectedRegion || undefined,
            filter_cluster: selectedCluster || undefined,
            filter_has_port: filterHasPort !== null ? filterHasPort : undefined,
            filter_has_planet: filterHasPlanet !== null ? filterHasPlanet : undefined,
            filter_discovered: filterDiscovered !== null ? filterDiscovered : undefined,
            search: searchQuery.trim() || undefined,
            page: currentPage,
            limit: itemsPerPage
          }
        });
        
        const data = response.data as { sectors: Sector[]; total?: number; total_count?: number; };
        setSectors(data.sectors || []);
        setTotalSectors(data.total ?? data.total_count ?? (data.sectors || []).length);
      } catch (error) {
        console.error('Error fetching sectors:', error);
        // If the API call fails, use empty array
        setSectors([]);
        setTotalSectors(0);
      } finally {
        setSectorLoading(false);
      }
    };
    
    fetchSectors();
  }, [
    galaxyState, 
    selectedRegion, 
    selectedCluster, 
    filterHasPort, 
    filterHasPlanet, 
    filterDiscovered, 
    searchQuery, 
    currentPage
  ]);
  
  // Load clusters when region is selected
  useEffect(() => {
    if (selectedRegion) {
      loadClusters(selectedRegion);
    } else if (regions.length > 0) {
      loadClusters();
    }
  }, [selectedRegion, regions]);
  
  // Reset cluster selection when region changes
  useEffect(() => {
    setSelectedCluster('');
  }, [selectedRegion]);
  
  // Handle region selection
  const handleRegionChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedRegion(e.target.value);
    setCurrentPage(1);
  };
  
  // Handle cluster selection
  const handleClusterChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedCluster(e.target.value);
    setCurrentPage(1);
  };
  
  // Handle search input
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
    setCurrentPage(1);
  };
  
  // Handle search submission
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setCurrentPage(1);
  };
  
  // Handle filter changes
  const handleFilterChange = (filter: 'port' | 'planet' | 'discovered', value: boolean | null) => {
    switch (filter) {
      case 'port':
        setFilterHasPort(value);
        break;
      case 'planet':
        setFilterHasPlanet(value);
        break;
      case 'discovered':
        setFilterDiscovered(value);
        break;
    }
    setCurrentPage(1);
  };
  
  // Reset all filters
  const resetFilters = () => {
    setSelectedRegion('');
    setSelectedCluster('');
    setFilterHasPort(null);
    setFilterHasPlanet(null);
    setFilterDiscovered(null);
    setSearchQuery('');
    setCurrentPage(1);
  };
  
  // Handle sector selection
  const handleSectorSelect = (sector: Sector) => {
    setSelectedSector(sector);
  };
  
  // Handle edit modal
  const handleEditSector = (sector: Sector) => {
    setEditingSector(sector);
    setIsEditModalOpen(true);
  };
  
  const handleCloseEditModal = () => {
    setIsEditModalOpen(false);
    setEditingSector(null);
  };
  
  const handleSaveSector = (updatedSector: Sector) => {
    // Update the sector in the list
    setSectors(prevSectors => 
      prevSectors.map(s => s.id === updatedSector.id ? updatedSector : s)
    );
    
    // Update selected sector if it was the one being edited
    if (selectedSector?.id === updatedSector.id) {
      setSelectedSector(updatedSector);
    }
    
    console.log('Sector updated successfully:', updatedSector.name);
  };
  
  return (
    <div className="page-container sectors-manager">
      <div className="page-header">
        <h1 className="page-title">Sectors Management</h1>
        <p className="page-subtitle">View and manage all sectors in the game universe.</p>
      </div>
      <div className="page-content">
        
        {error && (
          <div className="alert alert-error">
            {error}
          </div>
        )}
        
        <section className="section">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">Filters</h3>
            </div>
            <div className="card-body">
              <div className="grid grid-cols-2 gap-4 mb-4">
                <div className="form-group">
                  <label htmlFor="region-filter" className="form-label">Region</label>
                  <select
                    id="region-filter"
                    className="form-select"
                    value={selectedRegion}
                    onChange={handleRegionChange}
                  >
                    <option value="">All Regions</option>
                    {regions.map(region => (
                      <option key={region.id} value={region.id}>
                        {region.display_name} ({region.region_type})
                      </option>
                    ))}
                  </select>
                </div>
                
                <div className="form-group">
                  <label htmlFor="cluster-filter" className="form-label">Cluster</label>
                  <select 
                    id="cluster-filter" 
                    className="form-select"
                    value={selectedCluster}
                    onChange={handleClusterChange}
                    disabled={!selectedRegion}
                  >
                    <option value="">All Clusters</option>
                    {clusters.map(cluster => (
                      <option key={cluster.id} value={cluster.id}>
                        {cluster.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="form-group">
                  <label className="form-label">Has Port</label>
                  <div className="btn-group">
                    <button 
                      className={`btn btn-sm ${filterHasPort === true ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('port', filterHasPort === true ? null : true)}
                    >
                      Yes
                    </button>
                    <button 
                      className={`btn btn-sm ${filterHasPort === false ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('port', filterHasPort === false ? null : false)}
                    >
                      No
                    </button>
                    <button 
                      className={`btn btn-sm ${filterHasPort === null ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('port', null)}
                    >
                      Any
                    </button>
                  </div>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Has Planet</label>
                  <div className="btn-group">
                    <button 
                      className={`btn btn-sm ${filterHasPlanet === true ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('planet', filterHasPlanet === true ? null : true)}
                    >
                      Yes
                    </button>
                    <button 
                      className={`btn btn-sm ${filterHasPlanet === false ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('planet', filterHasPlanet === false ? null : false)}
                    >
                      No
                    </button>
                    <button 
                      className={`btn btn-sm ${filterHasPlanet === null ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('planet', null)}
                    >
                      Any
                    </button>
                  </div>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Discovered</label>
                  <div className="btn-group">
                    <button 
                      className={`btn btn-sm ${filterDiscovered === true ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('discovered', filterDiscovered === true ? null : true)}
                    >
                      Yes
                    </button>
                    <button 
                      className={`btn btn-sm ${filterDiscovered === false ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('discovered', filterDiscovered === false ? null : false)}
                    >
                      No
                    </button>
                    <button 
                      className={`btn btn-sm ${filterDiscovered === null ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => handleFilterChange('discovered', null)}
                    >
                      Any
                    </button>
                  </div>
                </div>
              </div>
            
              <div className="flex gap-4">
                <form className="flex-1" onSubmit={handleSearchSubmit}>
                  <div className="form-group">
                    <div className="input-group">
                      <input
                        type="text"
                        className="form-input"
                        placeholder="Search by sector name or ID..."
                        value={searchQuery}
                        onChange={handleSearchChange}
                      />
                      <button type="submit" className="btn btn-primary">Search</button>
                    </div>
                  </div>
                </form>
                
                <button className="btn btn-secondary" onClick={resetFilters}>
                  Reset Filters
                </button>
              </div>
            </div>
          </div>
        </section>
        
        <section className="section">
          {isLoading || sectorLoading ? (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading sectors data...</p>
            </div>
          ) : (
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">
                  Sectors (Showing {sectors.length.toLocaleString()} of {totalSectors.toLocaleString()})
                </h3>
              </div>
              <div className="card-body">
                {sectors.length === 0 ? (
                  <div className="empty-state">
                    <p>No sectors found matching your criteria.</p>
                  </div>
                ) : (
                  <>
                    <div className="table-container">
                      <table className="table table-hover">
                        <thead>
                          <tr>
                            <th>Sector</th>
                            <th>Coordinates</th>
                            <th>Features</th>
                            <th>Location</th>
                            <th>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sectors.map(sector => {
                            // Prefer server-resolved names; fall back to the
                            // already-loaded clusters context, then em-dash.
                            const regionName = sector.region_name || '—';
                            const clusterName =
                              sector.cluster_name ||
                              clusters.find(c => c.id === sector.cluster_id)?.name ||
                              '—';

                            return (
                              <tr 
                                key={sector.id} 
                                className={`cursor-pointer ${selectedSector?.id === sector.id ? 'table-row-selected' : ''}`}
                                onClick={() => {
                                  handleSectorSelect(sector);
                                  handleEditSector(sector);
                                }}
                              >
                                <td>
                                  <div className="flex flex-col">
                                    <span className="font-semibold text-primary">{sector.name}</span>
                                    <span className="text-sm text-muted font-mono">#{sector.sector_id}</span>
                                  </div>
                                </td>
                                
                                <td>
                                  <code className="text-sm">{sector.x_coord},{sector.y_coord},{sector.z_coord}</code>
                                </td>
                                
                                <td>
                                  <div className="flex gap-2 flex-wrap">
                                    {sector.has_port && <span className="badge badge-info" title="Trading Port">Port</span>}
                                    {sector.has_planet && <span className="badge badge-success" title="Habitable Planet">Planet</span>}
                                    {!sector.has_warp_tunnel && <span className="badge badge-warning" title="No Warp Tunnel">No Warp</span>}
                                    {sector.is_discovered && <span className="badge badge-success" title="Sector Mapped">Mapped</span>}
                                  </div>
                                </td>
                                
                                <td className="text-sm text-muted">
                                  {regionName} • {clusterName}
                                </td>
                                
                                <td>
                                  <button 
                                    className="btn btn-sm btn-primary"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleEditSector(sector);
                                    }}
                                  >
                                    Edit
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    
                    <div className="flex justify-between items-center mt-6">
                      <button
                        className="btn btn-secondary"
                        disabled={currentPage === 1}
                        onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
                      >
                        Previous
                      </button>
                      <span className="text-sm text-muted">
                        Page {currentPage} of {Math.max(1, Math.ceil(totalSectors / itemsPerPage))}
                      </span>
                      <button
                        className="btn btn-secondary"
                        disabled={currentPage >= Math.max(1, Math.ceil(totalSectors / itemsPerPage))}
                        onClick={() => setCurrentPage(prev => prev + 1)}
                      >
                        Next
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </section>
        
        {/* Sector Edit Modal */}
        <SectorEditModal
          sector={editingSector}
          isOpen={isEditModalOpen}
          onClose={handleCloseEditModal}
          onSave={handleSaveSector}
        />
      </div>
    </div>
  );
};

export default SectorsManager;