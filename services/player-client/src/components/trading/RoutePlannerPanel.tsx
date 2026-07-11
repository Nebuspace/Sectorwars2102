import React, { useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { formatCredits } from '../../utils/formatters';
import { resourceLabel } from '../../services/resourceCatalog';
import { routeOptimizerService, RouteOptimizeResponse, RouteHistoryEntry } from '../../services/routeOptimizerService';
import './route-planner.css';

type Objective = 'shortest' | 'profit' | 'risk' | 'balanced';

const OBJECTIVE_LABELS: Record<Objective, string> = {
  shortest: 'Shortest (fewest warps)',
  profit: 'Max Profit',
  risk: 'Min Risk',
  balanced: 'Balanced'
};

/** route_optimization_runs is shared with the ARIA /ai/optimize-route
 * endpoint (objective='ai_trading'), so a player's history can include
 * runs this panel didn't itself produce -- fall back to the raw value
 * rather than fabricate a label for an objective we don't recognize. */
const objectiveLabel = (objective: string): string =>
  OBJECTIVE_LABELS[objective as Objective] || objective;

const formatHistoryTimestamp = (iso: string): string => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};

type DisplayedRoute =
  | { kind: 'live'; data: RouteOptimizeResponse }
  | { kind: 'history'; data: RouteHistoryEntry };

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
  const [displayed, setDisplayed] = useState<DisplayedRoute | null>(null);

  // "Recent plans" strip -- collapsed by default (Scroll Law: opening the
  // outer panel must not itself push the Plot Route controls down), and
  // fetched lazily on its own first expand rather than whenever the panel
  // opens, so it costs nothing when the player never looks at it.
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [history, setHistory] = useState<RouteHistoryEntry[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

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
    setDisplayed(null);

    try {
      const response = await routeOptimizerService.optimizeRoute({
        startSectorId: start,
        endSectorId: objective === 'shortest' ? endSector.trim() : undefined,
        objective,
        cargoCapacity,
        maxRouteTime: 24.0,
        riskTolerance
      });
      setDisplayed({ kind: 'live', data: response });
    } catch (err: any) {
      // API error -> visible error state, never a fabricated route.
      setError(err?.message || 'Failed to optimize route.');
    } finally {
      setLoading(false);
    }
  };

  const toggleHistory = async () => {
    const expanding = historyCollapsed;
    setHistoryCollapsed(!historyCollapsed);
    if (!expanding || history !== null || historyLoading) return;

    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const rows = await routeOptimizerService.getHistory();
      setHistory(rows);
    } catch (err: any) {
      setHistoryError(err?.message || 'Failed to load recent plans.');
    } finally {
      setHistoryLoading(false);
    }
  };

  const viewHistoryEntry = (entry: RouteHistoryEntry) => {
    setError(null);
    setDisplayed({ kind: 'history', data: entry });
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
        <h3 aria-describedby="route-planner-subtitle">Trade Route Optimizer</h3>
        <span className="route-planner-toggle">{collapsed ? '▶' : '▼'}</span>
      </div>

      {/* WO-UIPC-ROUTEPLANNER-EXPLAINER: always visible (even collapsed) so a
          player scanning the Trading Hub gets the "commerce, not navigation"
          distinction without needing to open the panel first -- one tight
          line, Scroll-Law compact, matches the header's own muted label
          styling rather than a paragraph block. aria-describedby links it to
          the title above for screen readers (Pixel's a11y pass). */}
      <p id="route-planner-subtitle" className="route-planner-subtitle">
        Finds the most profitable buy-low/sell-high trade loops across the galaxy — this is commerce, not navigation. To plot a travel course, use NAV CHART.
      </p>

      {!collapsed && (
        <div className="route-planner-body">
          <div className="route-planner-history">
            <div
              className="route-planner-history-header"
              onClick={toggleHistory}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggleHistory();
                }
              }}
            >
              <span>Recent Plans{history && history.length > 0 ? ` (${history.length})` : ''}</span>
              <span className="route-planner-toggle">{historyCollapsed ? '▶' : '▼'}</span>
            </div>

            {!historyCollapsed && (
              <div className="route-planner-history-body">
                {historyLoading && (
                  <div className="route-planner-history-status">Loading recent plans...</div>
                )}
                {historyError && (
                  <div className="route-planner-error" role="alert">
                    <span className="error-icon">⚠️</span> {historyError}
                  </div>
                )}
                {!historyLoading && !historyError && history && history.length === 0 && (
                  <div className="route-planner-history-status">No route plans recorded yet.</div>
                )}
                {!historyLoading && history && history.length > 0 && (
                  <ul className="route-planner-history-list">
                    {history.map((entry) => (
                      <li key={entry.id}>
                        <button
                          type="button"
                          className="route-planner-history-entry"
                          onClick={() => viewHistoryEntry(entry)}
                        >
                          <span className="route-planner-history-objective">
                            {objectiveLabel(entry.objective)}
                          </span>
                          <span className="route-planner-history-route">
                            {entry.start_sector}
                            {entry.end_sector ? ` → ${entry.end_sector}` : ` (${entry.sectors.length} hops)`}
                          </span>
                          <span className="route-planner-history-profit">
                            {formatCredits(entry.total_profit)}
                          </span>
                          <span className="route-planner-history-time">
                            {formatHistoryTimestamp(entry.created_at)}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

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

          {displayed && (
            <div className="route-planner-result">
              {displayed.kind === 'history' && (
                <div className="route-planner-history-badge">
                  📋 Past result — plotted {formatHistoryTimestamp(displayed.data.created_at)}
                </div>
              )}

              <div className="route-planner-hops">
                {displayed.data.sectors.map((sector, index) => (
                  <React.Fragment key={`${sector}-${index}`}>
                    <span className="route-planner-hop">{sector}</span>
                    {index < displayed.data.sectors.length - 1 && (
                      <span className="route-planner-hop-arrow">→</span>
                    )}
                  </React.Fragment>
                ))}
              </div>

              <div className="route-planner-stats">
                <div className="route-planner-stat">
                  <span className="stat-label">Profit</span>
                  <span className="stat-value">{formatCredits(displayed.data.total_profit)}</span>
                </div>
                <div className="route-planner-stat">
                  <span className="stat-label">Time</span>
                  <span className="stat-value">{displayed.data.total_time_hours.toFixed(1)}h</span>
                </div>
                {displayed.kind === 'live' && (
                  <div className="route-planner-stat">
                    <span className="stat-label">Risk</span>
                    <span className="stat-value">{(displayed.data.total_risk * 100).toFixed(0)}%</span>
                  </div>
                )}
                <div className="route-planner-stat">
                  <span className="stat-label">Confidence</span>
                  <span className="stat-value">{(displayed.data.route_confidence * 100).toFixed(0)}%</span>
                </div>
              </div>

              {displayed.kind === 'live' && displayed.data.opportunities.length > 0 && (
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
                      {displayed.data.opportunities.map((opp, index) => (
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
