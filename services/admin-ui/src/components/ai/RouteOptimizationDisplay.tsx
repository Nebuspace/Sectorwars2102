import React, { useState, useEffect, useCallback } from 'react';
import { useAIUpdates } from '../../contexts/WebSocketContext';
import { api } from '../../utils/auth';
import './route-optimization-display.css';

interface OptimizedRoute {
  id: string;
  playerId: string;
  playerName: string;
  startSector: string;
  route: string[];
  estimatedProfit: number;
  estimatedTime: number;
  efficiency: number;
  status: string;
}

interface RouteStats {
  total_routes_optimized: number;
  avg_efficiency_improvement: number;
  avg_profit_increase: number;
  active_optimizations: number;
}

export const RouteOptimizationDisplay: React.FC = () => {
  const [activeRoutes, setActiveRoutes] = useState<OptimizedRoute[]>([]);
  const [routeStats, setRouteStats] = useState<RouteStats | null>(null);
  const [selectedRoute, setSelectedRoute] = useState<OptimizedRoute | null>(null);
  const [filterPurpose, setFilterPurpose] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handleRouteUpdate = useCallback((data: any) => {
    console.log('Route optimization update received:', data);
    setActiveRoutes(prev => {
      const updated = [...prev];
      const index = updated.findIndex(r => r.id === data.id);
      if (index >= 0) {
        updated[index] = data;
      } else {
        updated.unshift(data);
        if (updated.length > 50) updated.pop();
      }
      return updated;
    });
  }, []);

  const handleStatsUpdate = useCallback((data: any) => {
    console.log('Route stats update received:', data);
    setRouteStats(data);
  }, []);

  useAIUpdates(undefined, undefined, undefined, undefined, undefined, undefined, handleRouteUpdate, handleStatsUpdate);

  useEffect(() => {
    fetchActiveRoutes();
    fetchRouteStats();
  }, [filterPurpose]);

  const fetchActiveRoutes = async () => {
    try {
      setLoading(true);
      const response = await api.get('/api/v1/admin/ai/route-optimization');
      setActiveRoutes(response.data.active_optimizations || []);
    } catch (err: any) {
      const errorMessage = err.response?.data?.detail || err.message || 'Failed to load routes';
      if (err.response?.status === 401) {
        setError('Authentication required. Please log in as an admin user.');
      } else {
        setError(errorMessage);
      }
    } finally {
      setLoading(false);
    }
  };

  const fetchRouteStats = async () => {
    try {
      const response = await api.get('/api/v1/admin/ai/route-optimization');
      setRouteStats(response.data.optimization_stats);
    } catch (err) {
      console.error('Failed to load route stats:', err);
    }
  };

  const renderRouteVisualization = (route: OptimizedRoute) => {
    // In a real implementation, this would render an actual map
    return (
      <div className="route-visualization">
        <div className="route-path">
          <h4>Optimized Route</h4>
          <div className="route-nodes">
            {route.route.map((sector, index) => (
              <React.Fragment key={index}>
                <div className="route-node optimized">{sector}</div>
                {index < route.route.length - 1 && <div className="route-connector">→</div>}
              </React.Fragment>
            ))}
          </div>
        </div>
        <div className="route-benefits">
          <div className="benefit-item">
            <span className="benefit-icon">⏱️</span>
            <span className="benefit-value">{route.estimatedTime.toFixed(1)} hours</span>
          </div>
          <div className="benefit-item">
            <span className="benefit-icon">💰</span>
            <span className="benefit-value">{route.estimatedProfit.toLocaleString()} credits</span>
          </div>
          <div className="benefit-item">
            <span className="benefit-icon">📊</span>
            <span className="benefit-value">{route.efficiency}% efficiency</span>
          </div>
        </div>
      </div>
    );
  };

  if (loading) return <div className="loading">Loading route optimizations...</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <div className="route-optimization-display">
      <div className="route-header">
        <div className="route-stats-summary">
          {routeStats && (
            <>
              <div className="stat-item">
                <span className="stat-value">{routeStats.total_routes_optimized}</span>
                <span className="stat-label">Routes Optimized</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{routeStats.active_optimizations}</span>
                <span className="stat-label">Active Optimizations</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{routeStats.avg_efficiency_improvement.toFixed(1)}%</span>
                <span className="stat-label">Avg Cargo Efficiency</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{routeStats.avg_profit_increase.toLocaleString()} ₵</span>
                <span className="stat-label">Avg Route Profit</span>
              </div>
            </>
          )}
        </div>
        
        <div className="route-filters">
          <select value={filterPurpose} onChange={(e) => setFilterPurpose(e.target.value)}>
            <option value="all">All Purposes</option>
            <option value="trading">Trading</option>
            <option value="combat">Combat</option>
            <option value="exploration">Exploration</option>
            <option value="transport">Transport</option>
          </select>
        </div>
      </div>

      <div className="route-content">
        <div className="routes-list">
          <h3>Active Route Optimizations</h3>
          <div className="routes-table">
            <table>
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Start Sector</th>
                  <th>Route</th>
                  <th>Profit</th>
                  <th>Time</th>
                  <th>Efficiency</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {activeRoutes.map(route => (
                  <tr key={route.id}>
                    <td>{route.playerName}</td>
                    <td>{route.startSector}</td>
                    <td>
                      <span className="route-summary">
                        {route.route.length} sectors
                      </span>
                    </td>
                    <td>
                      <span className="profit-amount">
                        {route.estimatedProfit.toLocaleString()} ₵
                      </span>
                    </td>
                    <td>
                      <span className="time-estimate">
                        {route.estimatedTime.toFixed(1)}h
                      </span>
                    </td>
                    <td>
                      <span className={`efficiency ${route.efficiency >= 80 ? 'high' : route.efficiency >= 60 ? 'medium' : 'low'}`}>
                        {route.efficiency}%
                      </span>
                    </td>
                    <td>
                      <span className={`status-badge ${route.status}`}>
                        {route.status}
                      </span>
                    </td>
                    <td>
                      <button 
                        className="view-button"
                        onClick={() => setSelectedRoute(route)}
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {selectedRoute && (
          <div className="route-detail-modal">
            <div className="modal-content">
              <div className="modal-header">
                <h3>Route Optimization Details</h3>
                <button 
                  className="close-button"
                  onClick={() => setSelectedRoute(null)}
                >
                  ×
                </button>
              </div>
              
              <div className="modal-body">
                <div className="route-info">
                  <h4>{selectedRoute.playerName}</h4>
                  <p>Status: {selectedRoute.status}</p>
                  <p>Efficiency: {selectedRoute.efficiency}%</p>
                </div>

                {renderRouteVisualization(selectedRoute)}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};