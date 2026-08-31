import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ProductionMonitoring } from './ProductionMonitoring';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('react-chartjs-2', () => ({
  Line: () => <div data-testid="line-chart" />,
  Doughnut: () => <div data-testid="doughnut-chart" />,
}));

const productionPayload = {
  history: [
    {
      timestamp: '2026-08-31T12:00:00Z',
      fuel_ore: 1200,
      organics: 800,
      equipment: 450,
    },
  ],
  trends: [
    {
      resource: 'fuel_ore',
      current: 1200,
      average: 1200,
      peak: 1200,
      trend: 'stable' as const,
      efficiency: 95.0,
    },
    {
      resource: 'organics',
      current: 800,
      average: 800,
      peak: 800,
      trend: 'stable' as const,
      efficiency: 88.5,
    },
  ],
  alerts: [
    {
      id: 'planet-1-overflow-fuel_ore',
      type: 'overflow' as const,
      severity: 'high' as const,
      resource: 'fuel_ore',
      colony: 'Alpha Colony',
      message: 'Storage overflow at Alpha Colony: 50 fuel_ore wasted (cap 10,000)',
      timestamp: '2026-08-31T11:55:00Z',
    },
  ],
  stats: {
    totalProduction: { fuel_ore: 1200, organics: 800, equipment: 450 },
    topProducers: [
      {
        colonyId: '1',
        colonyName: 'Alpha Colony',
        resource: 'fuel_ore',
        amount: 1200,
      },
    ],
    bottlenecks: [
      {
        colonyId: '1',
        colonyName: 'Alpha Colony',
        issue: 'Storage overflow — production wasted',
        impact: 100,
      },
    ],
  },
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('ProductionMonitoring (LEG-3194)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads real-shaped commodity stockpiles and tick warnings', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: productionPayload });

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByText('Production Monitoring')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith(
      expect.stringMatching(
        /\/api\/v1\/admin\/colonization\/production\?timeRange=day&resource=all/
      )
    );
    expect(screen.getByText(/Storage overflow at Alpha Colony/)).toBeTruthy();
    expect(screen.getByText('Commodity Totals')).toBeTruthy();
    expect(screen.getByText(/Within cap: 95%/)).toBeTruthy();
    expect(screen.getByText('Commodity Stockpiles')).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('shows empty state when no tick warnings exist', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...productionPayload, alerts: [], stats: { ...productionPayload.stats, bottlenecks: [] } },
    });

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByTestId('production-alerts-empty')).toBeTruthy();
    });

    expect(screen.getByText(/No overflow or starvation warnings/)).toBeTruthy();
    expect(screen.getByTestId('production-bottlenecks-empty')).toBeTruthy();
  });

  it('reports a 403 as a scope problem, never as unimplemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/REGIONS_VIEW|regions view|Access denied/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toMatch(/route not found|proxy/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 429 as an admin rate-limit, not bare HTTP 429 load failure', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/Failed to load production data \(HTTP 429\)/);
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2955)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching production/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toMatch(/Failed to load production data/i);
  });

  it('collapses axios-shaped Network Error to gameserver-unreachable fallback (LEG-3337)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching production/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toContain('Failed to fetch');
    expect(alert).not.toContain('TypeError');
  });

  it('collapses non-TypeError Failed to fetch to gameserver-unreachable fallback (LEG-3337)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Failed to fetch'));

    render(<ProductionMonitoring />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching production/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toContain('Failed to fetch');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toContain('TypeError');
  });
});
