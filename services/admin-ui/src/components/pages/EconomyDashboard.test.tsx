import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import EconomyDashboard from './EconomyDashboard';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useEconomyUpdates: () => undefined,
}));

vi.mock('../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [{ name: 'ore', label: 'Ore' }],
    getLabel: (n: string) => n,
  }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('./EconomyLeversPanel', () => ({
  default: () => <div data-testid="levers-stub" />,
}));

vi.mock('./BountyAdminPanel', () => ({
  default: () => <div data-testid="bounty-stub" />,
}));

function d3Chain(): Record<string, unknown> {
  const api: Record<string, unknown> = {};
  const ret = () => api;
  for (const m of [
    'select',
    'selectAll',
    'append',
    'attr',
    'style',
    'text',
    'data',
    'enter',
    'exit',
    'remove',
    'call',
    'on',
    'transition',
    'duration',
    'domain',
    'range',
    'nice',
    'ticks',
    'tickFormat',
    'x',
    'y',
    'curve',
  ]) {
    api[m] = ret;
  }
  return api;
}

vi.mock('d3', () => ({
  select: () => d3Chain(),
  selectAll: () => d3Chain(),
  scaleLinear: () => d3Chain(),
  scaleBand: () => d3Chain(),
  axisBottom: () => () => undefined,
  axisLeft: () => () => undefined,
  line: () => d3Chain(),
  max: () => 1,
  min: () => 0,
}));

const okMarket = { data: [] as unknown[] };

const okMetrics = {
  data: {
    total_trade_volume: 1,
    total_credits_in_circulation: 2,
    average_profit_margin: 0.1,
    most_traded_commodity: 'ore',
    economic_health_score: 80,
  },
};

const okSummary = {
  data: {
    timestamp: '2026-08-20T00:00:00Z',
    health_score: 80,
    daily_summary: {
      total_transactions: 1,
      total_volume: 1,
      total_value: 1,
      unique_traders: 1,
    },
    key_metrics: {
      gdp: 1,
      money_supply: 1,
      market_velocity: 1,
      gini_coefficient: 0.2,
    },
    alert_summary: {
      total_alerts: 0,
      by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
      critical_alerts: [],
    },
    top_trading_ports: [],
  },
};

function httpErr(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status } });
}

describe('EconomyDashboard alerts/summary honesty (LEG-1372)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces price-alerts 403 as scope denial (not silent empty)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) throw httpErr(403);
      if (url.includes('/economy/dashboard-summary')) return okSummary;
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied/i);
    });
    expect(screen.getByRole('alert').textContent).toMatch(/alerts scope/i);
    expect(screen.getByRole('alert').textContent).not.toMatch(/Failed to load economic data/i);
  });

  it('surfaces price-alerts 429 as admin rate-limit copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) throw httpErr(429);
      if (url.includes('/economy/dashboard-summary')) return okSummary;
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('keeps empty alerts silent when price-alerts returns []', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) return okSummary;
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Economy Dashboard')).toBeTruthy();
      expect(screen.queryByRole('alert')).toBeNull();
    });
  });

  it('surfaces dashboard-summary 403 as scope denial', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) throw httpErr(403);
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied/i);
    });
    expect(screen.getByRole('alert').textContent).toMatch(/summary scope/i);
  });
});
