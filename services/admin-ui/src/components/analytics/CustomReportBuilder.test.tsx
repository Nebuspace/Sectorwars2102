import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { CustomReportBuilder } from './CustomReportBuilder';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const catalogPayload = {
  metrics: [
    {
      id: 'player_total_count',
      name: 'Total Players',
      category: 'Players',
      dataType: 'number',
      aggregations: ['count'],
      description: 'Total registered players (excludes soft-deleted accounts)',
    },
    {
      id: 'market_total_volume',
      name: 'Total Trade Volume (credits)',
      category: 'Economy',
      dataType: 'currency',
      aggregations: ['sum'],
      description: 'Sum of all transaction values in credits',
    },
  ],
};

const templatesPayload = {
  templates: [
    {
      id: 'tpl-economy-overview',
      name: 'Economy Overview',
      description: 'Key economic indicators',
      metrics: ['market_total_volume', 'player_total_count'],
      filters: [],
      groupBy: [],
      sortBy: [],
      visualization: 'table',
    },
  ],
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('CustomReportBuilder (LEG-143)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads catalog + templates via shared api and renders metrics', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/reports/metrics')) return { data: catalogPayload };
      if (url.includes('/reports/templates')) return { data: templatesPayload };
      throw new Error(`unexpected url ${url}`);
    });

    const onGenerate = vi.fn();
    render(<CustomReportBuilder onGenerate={onGenerate} />);

    await waitFor(() => {
      expect(screen.getByText('Total Players')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/reports/metrics');
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/reports/templates');
    expect(screen.getByText('Economy Overview')).toBeTruthy();
    expect(screen.getByText('Total Trade Volume (credits)')).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('reports a 403 as a scope problem, never as unimplemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<CustomReportBuilder onGenerate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('admin.audit.view');
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<CustomReportBuilder onGenerate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toMatch(/route not found|proxy/i);
    expect(alert).not.toContain('not implemented');
  });
});
