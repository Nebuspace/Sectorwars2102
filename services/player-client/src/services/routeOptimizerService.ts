// Route Optimizer Service (WO-SB-RO2 Lane C)
//
// Typed client for POST /api/v1/routes/optimize (src/api/routes/route_optimizer.py) —
// the graph-based optimizer, NOT /api/v1/ai/optimize-route (aiTradingService.optimizeRoute
// targets that separate ARIA-side endpoint; do not merge the two surfaces here).

export interface RouteOptimizeParams {
  startSectorId: string;
  endSectorId?: string;
  objective: 'shortest' | 'profit' | 'risk' | 'balanced';
  cargoCapacity: number;
  maxRouteTime: number;
  riskTolerance: number;
}

export interface RouteOpportunity {
  from_sector: string;
  to_sector: string;
  commodity: string;
  buy_price: number;
  sell_price: number;
  profit_per_unit: number;
  max_quantity: number;
  distance: number;
  travel_time_hours: number;
  risk_factor: number;
  confidence: number;
}

export interface RouteOptimizeResponse {
  objective: string;
  route_type: string;
  sectors: string[];
  total_profit: number;
  total_distance: number;
  total_time_hours: number;
  total_risk: number;
  cargo_efficiency: number;
  profit_per_hour: number;
  route_confidence: number;
  opportunities: RouteOpportunity[];
}

// One row from GET /api/v1/routes/history -- a past recorded run, mapped
// 1:1 from route_optimization_runs. Deliberately NOT shaped like
// RouteOptimizeResponse: total_risk/profit_per_hour/opportunities are never
// persisted for a run, so there is nothing honest to backfill them with.
export interface RouteHistoryEntry {
  id: string;
  objective: string;
  start_sector: string;
  end_sector: string | null;
  sectors: string[];
  total_profit: number;
  total_distance: number;
  total_time_hours: number;
  cargo_efficiency: number;
  route_confidence: number;
  status: string;
  created_at: string;
}

class RouteOptimizerService {
  private baseUrl = '/api/v1/routes';

  private getAuthHeaders() {
    const token = localStorage.getItem('accessToken');
    return {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    };
  }

  async optimizeRoute(params: RouteOptimizeParams): Promise<RouteOptimizeResponse> {
    const response = await fetch(`${this.baseUrl}/optimize`, {
      method: 'POST',
      headers: this.getAuthHeaders(),
      body: JSON.stringify({
        start_sector_id: params.startSectorId,
        end_sector_id: params.endSectorId || undefined,
        objective: params.objective,
        cargo_capacity: params.cargoCapacity,
        max_route_time: params.maxRouteTime,
        risk_tolerance: params.riskTolerance
      })
    });

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail || `Failed to optimize route: ${response.statusText}`);
    }

    return response.json();
  }

  /** The caller's own recorded route-optimization runs, newest first. */
  async getHistory(limit: number = 10): Promise<RouteHistoryEntry[]> {
    const response = await fetch(`${this.baseUrl}/history?limit=${limit}`, {
      method: 'GET',
      headers: this.getAuthHeaders()
    });

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail || `Failed to load route history: ${response.statusText}`);
    }

    return response.json();
  }
}

export const routeOptimizerService = new RouteOptimizerService();
export default routeOptimizerService;
