/**
 * @vitest-environment jsdom
 * routeOptimizerService — optimize + history fetch wiring and error mapping.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import routeOptimizerService from '../routeOptimizerService';

describe('routeOptimizerService', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('accessToken', 'tok-123');
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('POSTs optimize with snake_case body and auth header', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        objective: 'shortest',
        route_type: 'direct',
        sectors: ['A', 'B'],
        total_profit: 0,
        total_distance: 2,
        total_time_hours: 1,
        total_risk: 0.1,
        cargo_efficiency: 1,
        profit_per_hour: 0,
        route_confidence: 0.9,
        opportunities: [],
      }),
    });

    const result = await routeOptimizerService.optimizeRoute({
      startSectorId: 'sec-1',
      endSectorId: 'sec-2',
      objective: 'shortest',
      cargoCapacity: 100,
      maxRouteTime: 48,
      riskTolerance: 0.5,
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/routes/optimize', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer tok-123',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        start_sector_id: 'sec-1',
        end_sector_id: 'sec-2',
        objective: 'shortest',
        cargo_capacity: 100,
        max_route_time: 48,
        risk_tolerance: 0.5,
      }),
    });
    expect(result.sectors).toEqual(['A', 'B']);
  });

  it('throws detail from failed optimize responses', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      statusText: 'Bad Request',
      json: async () => ({ detail: 'no path' }),
    });
    await expect(
      routeOptimizerService.optimizeRoute({
        startSectorId: 'sec-1',
        objective: 'profit',
        cargoCapacity: 10,
        maxRouteTime: 10,
        riskTolerance: 1,
      }),
    ).rejects.toThrow('no path');
  });

  it('GETs history with limit and maps errors', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ id: 'r1', objective: 'shortest' }],
    });
    const rows = await routeOptimizerService.getHistory(5);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/routes/history?limit=5', {
      method: 'GET',
      headers: {
        Authorization: 'Bearer tok-123',
        'Content-Type': 'application/json',
      },
    });
    expect(rows[0].id).toBe('r1');

    fetchMock.mockResolvedValueOnce({
      ok: false,
      statusText: 'Server Error',
      json: async () => null,
    });
    await expect(routeOptimizerService.getHistory()).rejects.toThrow(
      'Failed to load route history: Server Error',
    );
  });
});
