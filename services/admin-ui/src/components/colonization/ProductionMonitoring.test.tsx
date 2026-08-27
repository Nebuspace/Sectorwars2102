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
      timestamp: '2026-08-16T12:00:00Z',
      energy: 100,
      minerals: 80,
      food: 120,
      water: 60,
    },
  ],
  trends: [
    {
      resource: 'energy',
      current: 100,
      average: 90,
      peak: 110,
      trend: 'stable' as const,
      efficiency: 90.9,
    },
  ],
  alerts: [],
  stats: {
    totalProduction: { energy: 100, minerals: 80, food: 120, water: 60 },
    topProducers: [
      {
        colonyId: '1',
        colonyName: 'Alpha Colony',
        resource: 'energy',
        amount: 50,
      },
    ],
    bottlenecks: [],
  },
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('ProductionMonitoring (LEG-144)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads production data via shared api and hydrates without not-implemented copy', async () => {
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
    expect(screen.getByText('Alpha Colony')).toBeTruthy();
    expect(screen.getByText('Resource Trends')).toBeTruthy();
    expect(screen.getByText(/Efficiency: 90\.9%/)).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
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
});
