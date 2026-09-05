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
  const chain = Object.assign(() => 0, {
    bandwidth: () => 10,
  }) as unknown as Record<string, unknown>;
  const ret = () => chain;
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
    'rangeRound',
    'paddingInner',
    'padding',
    'nice',
    'ticks',
    'tickFormat',
    'tickSize',
    'x',
    'y',
    'curve',
  ]) {
    chain[m] = ret;
  }
  return chain;
}

function scaleMock() {
  const scale = Object.assign(() => 0, d3Chain()) as (() => number) & Record<string, unknown>;
  scale.bandwidth = () => 10;
  return scale;
}

vi.mock('d3', () => ({
  select: () => d3Chain(),
  selectAll: () => d3Chain(),
  scaleLinear: () => scaleMock(),
  scaleBand: () => scaleMock(),
  axisBottom: () => d3Chain(),
  axisLeft: () => d3Chain(),
  line: () => d3Chain(),
  format: () => () => '0',
  max: (_data: unknown, fn?: (d: { avgBuy?: number; avgSell?: number; volume?: number }) => number) => {
    if (fn && Array.isArray(_data) && _data.length > 0) {
      return Math.max(
        ..._data.map((d) =>
          fn(d as { avgBuy: number; avgSell: number; volume: number }),
        ),
      );
    }
    return 100;
  },
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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3635 Soft-ORDER — EconomyDashboard TypeError/Network Error densify.
 * LEG-3860 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('EconomyDashboard typeErrorHonesty densify (LEG-3635)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collapses axios Network Error on dashboard-summary without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) {
        throw new Error('Network Error');
      }
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Dashboard summary unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on market-data secondary fetch without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) {
        throw new Error('Network Error');
      }
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) return okSummary;
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Market data unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on dashboard-summary without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Dashboard summary unavailable/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on market-data secondary fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) return okSummary;
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Market data unavailable/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with friendly scope copy when dashboard-summary GET is denied', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) {
        throw axiosError(403);
      }
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied|economy summary/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on dashboard-summary GET', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/economy/market-data')) return okMarket;
      if (url.includes('/economy/metrics')) return okMetrics;
      if (url.includes('/economy/price-alerts')) return { data: [] };
      if (url.includes('/economy/dashboard-summary')) {
        throw axiosError(429);
      }
      return { data: {} };
    });

    render(<EconomyDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
