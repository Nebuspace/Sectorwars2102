import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../../utils/auth';
import PageHeader from '../ui/PageHeader';
import PlanetDetailModal from '../universe/PlanetDetailModal';
import './pages.css';

interface Planet {
  id: string;
  name: string;
  sector_id: string;
  sector_name?: string;
  planet_type: string;
  population: number;
  max_population: number;
  defense_level: number;
  resource_production?: number;
  owner_id?: string;
  owner_name?: string;
  created_at: string;
  is_habitable?: boolean;
  atmosphere?: string;
  gravity?: number;
  // Enhanced fields from comprehensive API
  habitability_score?: number;
  resource_richness?: number;
  genesis_created?: boolean;
  colonized_at?: string;
}

const PlanetsManager: React.FC = () => {
  const [planets, setPlanets] = useState<Planet[]>([]);
  const [totalPlanets, setTotalPlanets] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage] = useState(20);
  const [selectedPlanet, setSelectedPlanet] = useState<Planet | null>(null);
  const [modalMode, setModalMode] = useState<'view' | 'edit'>('view');
  const [isModalOpen, setIsModalOpen] = useState(false);

  const fetchPlanets = useCallback(async () => {
    try {
      setLoading(true);
      // Fetch first batch with large limit to get all planets
      const pageSize = 100;
      const firstResponse = await api.get('/api/v1/admin/planets/comprehensive', {
        params: {
          page: 1,
          limit: pageSize,
          filter_type: filterType !== 'all' ? filterType : undefined,
          filter_colonized: filterType === 'colonized' ? true : filterType === 'uncolonized' ? false : undefined
        }
      });

      const totalCount = firstResponse.data.total_count || 0;
      const totalPages = firstResponse.data.total_pages || 1;
      let allPlanets = firstResponse.data.planets || [];

      // Fetch remaining pages if there are more
      if (totalPages > 1) {
        const remainingRequests = [];
        for (let page = 2; page <= totalPages; page++) {
          remainingRequests.push(
            api.get('/api/v1/admin/planets/comprehensive', {
              params: {
                page,
                limit: pageSize,
                filter_type: filterType !== 'all' ? filterType : undefined,
                filter_colonized: filterType === 'colonized' ? true : filterType === 'uncolonized' ? false : undefined
              }
            })
          );
        }
        const remainingResponses = await Promise.all(remainingRequests);
        for (const resp of remainingResponses) {
          allPlanets = allPlanets.concat(resp.data.planets || []);
        }
      }

      setPlanets(allPlanets);
      setTotalPlanets(totalCount);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to fetch planets');
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  useEffect(() => {
    fetchPlanets();
  }, [filterType, fetchPlanets]);

  const handleViewPlanet = (planet: Planet) => {
    setSelectedPlanet(planet);
    setModalMode('view');
    setIsModalOpen(true);
  };

  const handleEditPlanet = (planet: Planet) => {
    setSelectedPlanet(planet);
    setModalMode('edit');
    setIsModalOpen(true);
  };

  // Planet deletion is disabled: there is no backend route for
  // DELETE /api/v1/admin/planets/{id}. The control stays visible but inert
  // (and surfaces an inline notice) until the endpoint is implemented.
  const PLANET_DELETE_ENDPOINT = 'DELETE /api/v1/admin/planets/{id}';

  const handleDeletePlanet = (_planet: Planet) => {
    setError(`Planet deletion is unavailable: the backend endpoint ${PLANET_DELETE_ENDPOINT} is not implemented.`);
  };

  const handleModalClose = () => {
    setIsModalOpen(false);
    setSelectedPlanet(null);
  };

  const handlePlanetSave = (updatedPlanet: Planet) => {
    setPlanets(planets.map(p => p.id === updatedPlanet.id ? updatedPlanet : p));
    setIsModalOpen(false);
    setSelectedPlanet(null);
  };

  // Filter and search logic
  const filteredPlanets = planets.filter(planet => {
    const matchesSearch = planet.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         planet.sector_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         planet.owner_name?.toLowerCase().includes(searchTerm.toLowerCase());
    
    const matchesFilter = filterType === 'all' || 
                         (filterType === 'habitable' && planet.is_habitable) ||
                         (filterType === 'uninhabitable' && !planet.is_habitable) ||
                         (filterType === 'colonized' && planet.owner_id) ||
                         (filterType === 'uncolonized' && !planet.owner_id);
    
    return matchesSearch && matchesFilter;
  });

  // Pagination
  const totalPages = Math.ceil(filteredPlanets.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedPlanets = filteredPlanets.slice(startIndex, startIndex + itemsPerPage);

  if (loading) {
    return (
      <div className="page-container">
        <PageHeader title="Planets Manager" subtitle="Comprehensive Planet Administration" />
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Loading planets...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader title="Planets Manager" subtitle="Comprehensive Planet Administration" />
      
      {error && (
        <div className="error-message">
          <p>{error}</p>
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}

      {/* Search and Filter Controls */}
      <div className="table-controls">
        <div className="search-section">
          <input
            type="text"
            placeholder="Search planets, sectors, or owners..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
        </div>
        
        <div className="filter-section">
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            className="filter-select"
          >
            <option value="all">All Planets</option>
            <option value="habitable">Habitable</option>
            <option value="uninhabitable">Uninhabitable</option>
            <option value="colonized">Colonized</option>
            <option value="uncolonized">Uncolonized</option>
          </select>
        </div>

        <div className="results-info">
          <span>{filteredPlanets.length} of {totalPlanets} planets</span>
        </div>
      </div>

      {/* Planets Table */}
      <div className="crud-table-container">
        <table className="crud-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Sector</th>
              <th>Type</th>
              <th>Population</th>
              <th>Habitability</th>
              <th>Resources</th>
              <th>Defense</th>
              <th>Owner</th>
              <th>Genesis</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {paginatedPlanets.map(planet => (
              <tr key={planet.id}>
                <td className="name-cell">
                  <strong>{planet.name}</strong>
                </td>
                <td>{planet.sector_name || planet.sector_id}</td>
                <td>
                  <span className={`planet-type ${planet.planet_type.toLowerCase()}`}>
                    {planet.planet_type}
                  </span>
                </td>
                <td>
                  {planet.population.toLocaleString()} / {planet.max_population.toLocaleString()}
                </td>
                <td>
                  <span className={`habitability-score score-${Math.floor((planet.habitability_score || 0) / 20)}`}>
                    {planet.habitability_score || 'N/A'}%
                  </span>
                </td>
                <td>
                  <span className={`resource-richness richness-${Math.floor((planet.resource_richness || 0) * 2)}`}>
                    {planet.resource_richness ? `${planet.resource_richness.toFixed(1)}x` : 'N/A'}
                  </span>
                </td>
                <td>
                  <span className={`defense-level level-${Math.floor(planet.defense_level / 20)}`}>
                    {planet.defense_level}
                  </span>
                </td>
                <td>{planet.owner_name || 'Uncolonized'}</td>
                <td>
                  <span className={`status ${planet.genesis_created ? 'genesis' : 'natural'}`}>
                    {planet.genesis_created ? '🧬 Genesis' : '🌍 Natural'}
                  </span>
                </td>
                <td>
                  <div className="action-buttons">
                    <button 
                      className="view-btn" 
                      title="View Details"
                      onClick={() => handleViewPlanet(planet)}
                    >
                      👁️
                    </button>
                    <button 
                      className="edit-btn" 
                      title="Edit Planet"
                      onClick={() => handleEditPlanet(planet)}
                    >
                      ✏️
                    </button>
                    <button
                      className="delete-btn"
                      title={`Disabled — missing backend endpoint ${PLANET_DELETE_ENDPOINT}`}
                      disabled
                      style={{ opacity: 0.5, cursor: 'not-allowed' }}
                      onClick={() => handleDeletePlanet(planet)}
                    >
                      🗑️
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="pagination">
          <button 
            onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
            disabled={currentPage === 1}
            className="pagination-btn"
          >
            Previous
          </button>
          
          <span className="pagination-info">
            Page {currentPage} of {totalPages}
          </span>
          
          <button 
            onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages))}
            disabled={currentPage === totalPages}
            className="pagination-btn"
          >
            Next
          </button>
        </div>
      )}

      {/* Planet Detail Modal */}
      <PlanetDetailModal
        planet={selectedPlanet}
        isOpen={isModalOpen}
        onClose={handleModalClose}
        onSave={handlePlanetSave}
        mode={modalMode}
      />
    </div>
  );
};

export default PlanetsManager;