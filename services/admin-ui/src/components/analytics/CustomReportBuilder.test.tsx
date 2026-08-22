import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { CustomReportBuilder } from './CustomReportBuilder';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const liveMetrics = {
  metrics: [
    {
      id: 'player_total_count',
      name: 'Total Players',
      category: 'Players',
      dataType: 'number' as const,
      aggregations: ['count'],
      description: 'Total registered players (excludes soft-deleted accounts)',
    },
    {
      id: 'market_total_volume',
      name: 'Total Trade Volume (credits)',
      category: 'Economy',
      dataType: 'currency' as const,
      aggregations: ['sum'],
      description: 'Sum of all transaction values in credits',
    },
  ],
};

const liveTemplates = {
  templates: [
    {
      id: 'tpl-economy-overview',
      name: 'Economy Overview',
      description: 'Key economic indicators',
      metrics: ['market_total_volume', 'player_total_count'],
      filters: [],
      groupBy: [],
      sortBy: [],
      visualization: 'table' as const,
    },
  ],
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status } });

describe('CustomReportBuilder (LEG-162)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('hydrates metrics and templates from the shipped endpoints via shared api', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/api/v1/admin/reports/metrics') return { data: liveMetrics };
      if (url === '/api/v1/admin/reports/templates') return { data: liveTemplates };
      throw new Error(`unexpected url ${url}`);
    });

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText('Economy Overview')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/reports/metrics');
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/reports/templates');

    expect(screen.getByText('Total Players')).toBeTruthy();
    expect(screen.getByText('Total Trade Volume (credits)')).toBeTruthy();
    expect(screen.getByText('Key economic indicators')).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('reports a 403 as a scope problem, never as not-implemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('admin.audit.view');
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 429 as an admin rate-limit, distinct from 404 honesty', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toContain('404');
    expect(alert).not.toContain('HTTP 429');
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toContain('ship in the gameserver');
    expect(alert).not.toContain('not implemented');
  });

  it('distinguishes an unreachable gameserver from an HTTP error', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('unreachable');
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 429 as an admin rate-limit, distinct from 404 honesty', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toContain('404');
    expect(alert).not.toContain('HTTP 429');
  });

});
