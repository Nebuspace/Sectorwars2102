import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AdvancedAnalytics } from './AdvancedAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../analytics/CustomReportBuilder', () => ({
  CustomReportBuilder: ({
    onGenerate,
  }: {
    onGenerate: (template: unknown) => void;
  }) => (
    <button
      type="button"
      data-testid="trigger-generate"
      onClick={() =>
        onGenerate({
          id: 'custom-1',
          name: 'Ops Snapshot',
          description: '',
          metrics: ['player_total_count'],
          filters: [],
          groupBy: [],
          sortBy: [],
          visualization: 'table',
        })
      }
    >
      Generate fixture
    </button>
  ),
}));

vi.mock('../analytics/PredictiveAnalytics', () => ({
  PredictiveAnalytics: () => <div data-testid="predictive-stub" />,
}));

vi.mock('../analytics/PerformanceMetrics', () => ({
  PerformanceMetrics: () => <div data-testid="performance-stub" />,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : undefined },
  });

describe('AdvancedAnalytics generate/export (LEG-165)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('posts generate via shared api and shows the returned report', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        id: 'report-abc123',
        name: 'Ops Snapshot',
        generatedAt: '2026-08-16T12:00:00Z',
        data: { player_total_count: 42 },
        template: { name: 'Ops Snapshot', metrics: ['player_total_count'] },
      },
    });

    render(<AdvancedAnalytics />);

    fireEvent.click(screen.getByTestId('trigger-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('generated-reports')).toBeTruthy();
    });

    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/admin/reports/generate',
      expect.objectContaining({
        name: 'Ops Snapshot',
        metrics: ['player_total_count'],
      }),
    );
    expect(screen.getByText('Ops Snapshot')).toBeTruthy();
    expect(screen.getByTestId('analytics-save-message').textContent).toMatch(
      /generated successfully/i,
    );
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('reports generate 403 without claiming the endpoint is unimplemented', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toContain('admin.audit.view');
    expect(msg).not.toContain('not implemented');
  });

  it('reports generate 429 with reports-tier rate-limit copy', async () => {

    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toMatch(/5\/hour/i);
    expect(msg).toMatch(/rate limit/i);
    expect(msg).not.toMatch(/HTTP 429/i);
    expect(msg).not.toMatch(/gameserver unreachable|not implemented/i);
  });

  it('reports generate 404 as a routing fault, never as not-implemented', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(404));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toMatch(/route not found \(404\)/i);
    expect(msg).not.toContain('not implemented');
  });

  it('reports generate network errors without inventing an unimplemented endpoint', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toMatch(/network error/i);
    expect(msg).not.toContain('not implemented');
  });

  it('surfaces honest generate fallback on TypeError/network collapse (LEG-3033)', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toMatch(/gameserver unreachable \(network error\)/i);
    expect(msg).toMatch(/Failed to generate report/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
    expect(msg).not.toContain('not implemented');
  });

  it('exports via shared api with blob responseType', async () => {
    const blob = new Blob(['a,b\n1,2\n'], { type: 'text/csv' });
    vi.mocked(api.get).mockResolvedValue({
      data: blob,
      headers: { 'content-type': 'text/csv' },
    });

    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));

    await waitFor(() => {
      expect(screen.getByText('Player Data')).toBeTruthy();
    });

    const cardExport = screen
      .getByText('Player Data')
      .closest('.export-card')
      ?.querySelector('button.btn-primary');
    expect(cardExport).toBeTruthy();
    fireEvent.click(cardExport!);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/analytics/export', {
        params: { dataset: 'players', format: 'csv' },
        responseType: 'blob',
      });
    });
    expect(createObjectURL).toHaveBeenCalled();
  });

  it('reports export 403 honestly without not-implemented copy', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));

    await waitFor(() => {
      expect(screen.getByText('Player Data')).toBeTruthy();
    });

    const cardExport = screen
      .getByText('Player Data')
      .closest('.export-card')
      ?.querySelector('button.btn-primary');
    fireEvent.click(cardExport!);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toContain('admin.audit.view');
    expect(msg).not.toContain('not implemented');
  });

  it('reports export GET 429 with reports-tier rate-limit copy (LEG-2891)', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));

    await waitFor(() => {
      expect(screen.getByText('Player Data')).toBeTruthy();
    });

    const cardExport = screen
      .getByText('Player Data')
      .closest('.export-card')
      ?.querySelector('button.btn-primary');
    fireEvent.click(cardExport!);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toMatch(/5\/hour/i);
    expect(msg).toMatch(/rate limit/i);
    expect(msg).not.toMatch(/^Export failed/i);
    expect(msg).not.toContain('not implemented');
  });

  it('surfaces honest export fallback on TypeError/network collapse (LEG-3033)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));

    await waitFor(() => {
      expect(screen.getByText('Player Data')).toBeTruthy();
    });

    const cardExport = screen
      .getByText('Player Data')
      .closest('.export-card')
      ?.querySelector('button.btn-primary');
    fireEvent.click(cardExport!);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });

    const msg = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(msg).toMatch(/gameserver unreachable \(network error\)/i);
    expect(msg).toMatch(/Export failed/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
    expect(msg).not.toContain('not implemented');
  });
});
