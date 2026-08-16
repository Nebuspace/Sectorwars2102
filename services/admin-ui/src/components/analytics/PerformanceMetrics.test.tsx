import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { PerformanceMetrics } from './PerformanceMetrics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

// chart.js needs a real canvas; jsdom has none. The assertions here are about
// which numbers and labels the component binds, not about rendered pixels.
vi.mock('react-chartjs-2', () => ({
  Line: ({ data }: { data: { datasets: Array<{ label: string }> } }) => (
    <div data-testid="line-chart" data-series={data.datasets.map(d => d.label).join('|')} />
  ),
  Doughnut: ({ data }: { data: { labels: string[]; datasets: Array<{ data: number[] }> } }) => (
    <div
      data-testid="doughnut-chart"
      data-labels={data.labels.join('|')}
      data-values={data.datasets[0].data.join('|')}
    />
  ),
}));

// Mirrors a real GET /api/v1/admin/performance/metrics response: genuine
// Postgres-sourced numbers, and the literal zeros the route hardcodes for
// everything it cannot actually measure.
const livePayload = {
  system: {
    serverLoad: 0.0,
    memoryUsage: 0.0,
    diskUsage: 0.0,
    networkLatency: 0.0,
    activeConnections: 14,
    requestsPerSecond: 0.25,
    errorRate: 0.0,
    uptime: 20.0, // 20% of a 30-day window == 6 days of postmaster age
  },
  database: {
    queryTime: 0.0,
    activeQueries: 3,
    slowQueries: 0,
    connectionPool: { active: 3, idle: 9, total: 14 },
    cacheHitRate: 98.7,
  },
  application: {
    responseTime: { p50: 0, p95: 0, p99: 0 },
    throughput: 0.0125,
    errorCount: 0,
    successRate: 100.0,
    endpoints: [{ path: '/trading/ore', avgTime: 0, calls: 412, errors: 0 }],
  },
  historical: {
    timestamps: ['08:00', '10:00'],
    serverLoad: [0.0, 0.0],
    responseTime: [12, 31],
    errorRate: [0.0, 0.0],
  },
  suggestions: [],
};

const cardFor = (label: string) => screen.getByText(label).closest('.metric-card');
const statFor = (label: string) => screen.getByText(label).closest('.stat-item');

const axiosError = (status: number) => Object.assign(new Error(`HTTP ${status}`), {
  response: { status },
});

describe('PerformanceMetrics (LEG-114)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('binds the live payload from the shipped endpoint via the shared api client', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('Active DB Connections')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/performance/metrics', {
      params: { timeRange: '24h' },
    });

    // Real system + database values render as numbers.
    expect(cardFor('Active DB Connections')!.textContent).toContain('14');
    expect(cardFor('Market Trades / sec')!.textContent).toContain('0.250');
    expect(statFor('Active Queries')!.textContent).toContain('3');
    expect(statFor('Cache Hit Rate')!.textContent).toContain('98.7%');

    // Connection pool: active/idle/other, never a negative "available" slice.
    const doughnut = screen.getByTestId('doughnut-chart');
    expect(doughnut.getAttribute('data-values')).toBe('3|9|2');
    expect(doughnut.getAttribute('data-labels')).toBe('Active|Idle|Other states');
  });

  it('presents uptime as Postgres process age, not an availability SLA', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('Postgres Process Age')).toBeTruthy();
    });

    const card = cardFor('Postgres Process Age')!;
    // 20% of a 30-day window is 6 days of process age.
    expect(card.textContent).toContain('6d 0h');
    expect(card.textContent).toContain('not an availability SLA');
    // The old bogus derivation reported fabricated annual downtime.
    expect(card.textContent).not.toContain('downtime/year');
  });

  it('renders unmeasurable fields as n/a in a neutral state, never as a healthy zero', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('Server Load')).toBeTruthy();
    });

    for (const label of ['Server Load', 'Memory Usage', 'Disk Usage', 'Network Latency']) {
      const card = cardFor(label)!;
      expect(card.textContent).toContain('n/a');
      expect(card.textContent).toContain('psutil not installed on the gameserver');
      // A 0 that was never measured must not be dressed up as a healthy reading.
      expect(card.textContent).not.toContain('0.0%');
      expect(card.classList.contains('good')).toBe(false);
      expect(card.classList.contains('unavailable')).toBe(true);
    }

    // Response-time percentiles are hardcoded zeros server-side — no fake bars.
    expect(screen.getByText(/P50 \/ P95 \/ P99 are/).textContent).toContain(
      'no in-band request timing'
    );
    expect(screen.queryByText('0ms')).toBeNull();

    // queryTime is zero only because pg_stat_statements is absent.
    const queryTime = statFor('Average Query Time')!;
    expect(queryTime.textContent).toContain('n/a');
    expect(queryTime.textContent).toContain('pg_stat_statements');
  });

  it('still renders a genuinely measured zero as a number', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('Slow Queries (>1s)')).toBeTruthy();
    });

    // slowQueries: 0 is a real observation (no slow queries right now), so it
    // must show 0 — proving n/a is decided per field, not by value === 0.
    const slow = statFor('Slow Queries (>1s)')!;
    expect(slow.textContent).toContain('0');
    expect(slow.textContent).not.toContain('n/a');
  });

  it('labels the trend chart as transaction volume and omits the zero-padded series', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByTestId('line-chart')).toBeTruthy();
    });

    const series = screen.getByTestId('line-chart').getAttribute('data-series');
    expect(series).toBe('Market transactions per bucket');
    // The historical.responseTime array is transaction counts, not milliseconds.
    expect(series).not.toContain('ms');
    expect(series).not.toContain('Server Load');
    expect(series).not.toContain('Error Rate');
  });

  it('reports a 403 as a scope problem', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('admin.audit.view');
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toContain('ships in the gameserver');
    // Regression guard: the route is shipped, so this copy must never return.
    expect(alert).not.toContain('not implemented');
  });

  it('distinguishes an unreachable gameserver from an HTTP error', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<PerformanceMetrics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent).toContain('Gameserver unreachable');
  });
});
