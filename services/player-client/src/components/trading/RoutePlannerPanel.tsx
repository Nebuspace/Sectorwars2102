import React, { useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { formatCredits } from '../../utils/formatters';
import { resourceLabel } from '../../services/resourceCatalog';
import { routeOptimizerService, RouteOptimizeResponse } from '../../services/routeOptimizerService';
import './route-planner.css';

type Objective = 'shortest' | 'profit' | 'risk' | 'balanced';

const OBJECTIVE_LABELS: Record<Objective, string> = {
  shortest: 'Shortest (fewest warps)',
  profit: 'Max Profit',
  risk: 'Min Risk',
  balanced: 'Balanced'
};

/** Ship cargo capacity, mirroring TradingInterface's getCargoCapacity(). */
const getShipCargoCapacity = (currentShip: any): number => {
  if (!currentShip) return 100;
  if (currentShip.cargo && typeof currentShip.cargo === 'object' && 'capacity' in currentShip.cargo) {
    return Number(currentShip.cargo.capacity) || 100;
  }
  return currentShip.cargo_capacity || 100;
};

/**
 * First player consumer of POST /api/v1/routes/optimize — the graph-based
 * route optimizer (route_optimizer.py), NOT the ARIA /ai/optimize-route
 * surface aiTradingService already exposes elsewhere.
 *
 * Mounted as a collapsible section inside TradingInterface so it never
 * pushes the primary buy/sell grid below the fold (Scroll Law) when closed.
 */
const RoutePlannerPanel: React.FC = () => {
  const { playerState, currentShip } = useGame();

  const [collapsed, setCollapsed] = useState(true);
  const [objective, setObjective] = useState<Objective>('balanced');
  const [startSector, setStartSector] = useState<string>(
    playerState?.current_sector_id ? String(playerState.current_sector_id) : ''
  );
  const [endSector, setEndSector] = useState<string>('');
  const [cargoCapacity, setCargoCapacity] = useState<number>(getShipCargoCapacity(currentShip));
  const [riskTolerance, setRiskTolerance] = useState<number>(0.5);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RouteOptimizeResponse | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;

    const start = startSector.trim();
    if (!start) {
      setError('Start sector is required.');
      return;
    }
    if (objective === 'shortest' && !endSector.trim()) {
      setError("End sector is required for the 'shortest' objective.");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await routeOptimizerService.optimizeRoute({
        startSectorId: start,
        endSectorId: objective === 'shortest' ? endSector.trim() : undefined,
        objective,
        cargoCapacity,
        maxRouteTime: 24.0,
        riskTolerance
      });
      setResult(response);
    } catch (err: any) {
      // API error -> visible error state, never a fabricated route.
      setError(err?.message || 'Failed to optimize route.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="route-planner-panel">
      <div
        className="route-planner-header"
        onClick={() => setCollapsed(!collapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setCollapsed(!collapsed);
          }
        }}
      >
        <h3>Route Planner</h3>
        <span className="route-planner-toggle">{collapsed ? '▶' : '▼'}</span>
      </div>

      {!collapsed && (
        <div className="route-planner-body">
          <form className="route-planner-form" onSubmit={handleSubmit}>
            <div className="route-planner-field">
              <label htmlFor="rp-objective">Objective</label>
              <select
                id="rp-objective"
                value={objective}
                onChange={(e) => setObjective(e.target.value as Objective)}
              >
                {(Object.keys(OBJECTIVE_LABELS) as Objective[]).map((key) => (
                  <option key={key} value={key}>{OBJECTIVE_LABELS[key]}</option>
                ))}
              </select>
            </div>

            <div className="route-planner-field-row">
              <div className="route-planner-field">
                <label htmlFor="rp-start">Start Sector</label>
                <input
                  id="rp-start"
                  type="text"
                  value={startSector}
                  onChange={(e) => setStartSector(e.target.value)}
                  placeholder="Sector #"
                />
              </div>
              <div className="route-planner-field">
                <label htmlFor="rp-end">
                  End Sector {objective === 'shortest' ? '' : '(optional)'}
                </label>
                <input
                  id="rp-end"
                  type="text"
                  value={endSector}
                  onChange={(e) => setEndSector(e.target.value)}
                  placeholder="Sector #"
                  disabled={objective !== 'shortest'}
                />
              </div>
            </div>

            <div className="route-planner-field">
              <label htmlFor="rp-cargo">
                Cargo Capacity: {cargoCapacity} units
              </label>
              <input
                id="rp-cargo"
                type="range"
                min="1"
                max={Math.max(1000, cargoCapacity)}
                value={cargoCapacity}
                onChange={(e) => setCargoCapacity(parseInt(e.target.value, 10))}
                disabled={objective === 'shortest'}
              />
            </div>

            <div className="route-planner-field">
              <label htmlFor="rp-risk">
                Risk Tolerance: {(riskTolerance * 100).toFixed(0)}%
              </label>
              <input
                id="rp-risk"
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={riskTolerance}
                onChange={(e) => setRiskTolerance(parseFloat(e.target.value))}
                disabled={objective === 'shortest'}
              />
            </div>

            <button type="submit" className="route-planner-submit" disabled={loading}>
              {loading ? 'Plotting...' : 'Plot Route'}
            </button>
          </form>

          {error && (
            <div className="route-planner-error" role="alert">
              <span className="error-icon">⚠️</span> {error}
            </div>
          )}

          {result && (
            <div className="route-planner-result">
              <div className="route-planner-hops">
                {result.sectors.map((sector, index) => (
                  <React.Fragment key={`${sector}-${index}`}>
                    <span className="route-planner-hop">{sector}</span>
                    {index < result.sectors.length - 1 && (
                      <span className="route-planner-hop-arrow">→</span>
                    )}
                  </React.Fragment>
                ))}
              </div>

              <div className="route-planner-stats">
                <div className="route-planner-stat">
                  <span className="stat-label">Profit</span>
                  <span className="stat-value">{formatCredits(result.total_profit)}</span>
                </div>
                <div className="route-planner-stat">
                  <span className="stat-label">Time</span>
                  <span className="stat-value">{result.total_time_hours.toFixed(1)}h</span>
                </div>
                <div className="route-planner-stat">
                  <span className="stat-label">Risk</span>
                  <span className="stat-value">{(result.total_risk * 100).toFixed(0)}%</span>
                </div>
                <div className="route-planner-stat">
                  <span className="stat-label">Confidence</span>
                  <span className="stat-value">{(result.route_confidence * 100).toFixed(0)}%</span>
                </div>
              </div>

              {result.opportunities.length > 0 && (
                <div className="route-planner-opportunities">
                  <table>
                    <thead>
                      <tr>
                        <th>From</th>
                        <th>To</th>
                        <th>Commodity</th>
                        <th>Buy</th>
                        <th>Sell</th>
                        <th>Profit/Unit</th>
                        <th>Max Qty</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.opportunities.map((opp, index) => (
                        <tr key={`${opp.from_sector}-${opp.to_sector}-${index}`}>
                          <td>{opp.from_sector}</td>
                          <td>{opp.to_sector}</td>
                          <td>{resourceLabel(opp.commodity)}</td>
                          <td>{formatCredits(opp.buy_price)}</td>
                          <td>{formatCredits(opp.sell_price)}</td>
                          <td>{formatCredits(opp.profit_per_unit)}</td>
                          <td>{opp.max_quantity}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default RoutePlannerPanel;
