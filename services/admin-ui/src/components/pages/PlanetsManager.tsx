import React, { useState, useEffect, useCallback, useMemo } from 'react';
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
      const response = await api.get('/api/v1/admin/planets/comprehensive', {
        params: {
          page: currentPage,
          limit: itemsPerPage,
          filter_type: filterType !== 'all' ? filterType : undefined,
          filter_colonized: filterType === 'colonized' ? true : filterType === 'uncolonized' ? false : undefined,
          search: searchTerm || undefined
        }
      });

      setPlanets(response.data.planets || []);
      setTotalPlanets(response.data.total_count || 0);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to fetch planets');
    } finally {
      setLoading(false);
    }
  }, [currentPage, itemsPerPage, filterType, searchTerm]);

  useEffect(() => {
    fetchPlanets();
  }, [fetchPlanets]);

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

  const handleDeletePlanet = async (planet: Planet) => {
    if (!window.confirm(`Delete planet "${planet.name}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/api/v1/admin/planets/${planet.id}`);
      setPlanets(prev => prev.filter(p => p.id !== planet.id));
      setTotalPlanets(prev => prev - 1);
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to delete planet "${planet.name}"`);
    }
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
  const normalizedSearchTerm = searchTerm.trim().toLowerCase();
  const filteredPlanets = useMemo(() => planets.filter(planet => {
    const planetName = planet.name.toLowerCase();
    const sectorName = planet.sector_name?.toLowerCase() ?? '';
    const ownerName = planet.owner_name?.toLowerCase() ?? '';

    const matchesSearch = planetName.includes(normalizedSearchTerm) ||
                         sectorName.includes(normalizedSearchTerm) ||
                         ownerName.includes(normalizedSearchTerm);
    
    const matchesFilter = filterType === 'all' || 
                         (filterType === 'habitable' && planet.is_habitable) ||
                         (filterType === 'uninhabitable' && !planet.is_habitable) ||
                         (filterType === 'colonized' && planet.owner_id) ||
                         (filterType === 'uncolonized' && !planet.owner_id);
    
    return matchesSearch && matchesFilter;
  }), [planets, normalizedSearchTerm, filterType]);

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
                      title="Delete Planet"
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