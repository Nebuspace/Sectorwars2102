import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EconomyDashboard from './EconomyDashboard';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
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
  const api = Object.assign(() => 0, {
    bandwidth: () => 10,
  }) as unknown as Record<string, unknown>;
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
    api[m] = ret;
  }
  return api;
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

function httpErr(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });
}

const okMarketRow = [
  {
    station_id: 'st1',
    port_name: 'Port Alpha',
    sector_name: 'S1',
    commodity: 'ore',
    buy_price: 100,
    sell_price: 120,
    quantity: 50,
    last_updated: '2026-08-20T00:00:00Z',
  },
];

function mockSuccessfulLoads(marketData: unknown[] = okMarketRow) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/economy/market-data')) return { data: marketData };
    if (url.includes('/economy/metrics')) return okMetrics;
    if (url.includes('/economy/price-alerts')) return { data: [] };
    if (url.includes('/economy/dashboard-summary')) return okSummary;
    return { data: {} };
  });
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

describe('EconomyDashboard mutation errors (LEG-2600)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.delete).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
    mockSuccessfulLoads();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces formatAdminApiError on price intervention POST 403', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'prompt').mockReturnValue('150');
    vi.mocked(api.post).mockRejectedValue(
      httpErr(403, 'Missing scope admin.economy.intervene'),
    );

    render(<EconomyDashboard />);
    await waitFor(() => expect(screen.getByText('Port Alpha')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /Intervene/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/economy/intervention',
        expect.objectContaining({ intervention_type: 'price_adjustment' }),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.economy.intervene');
    expect(toastError).not.toHaveBeenCalledWith('Price intervention failed.');
  });

  it('surfaces rate-limit copy on price intervention POST 429', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'prompt').mockReturnValue('150');
    vi.mocked(api.post).mockRejectedValue(httpErr(429));

    render(<EconomyDashboard />);
    await waitFor(() => expect(screen.getByText('Port Alpha')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /Intervene/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Price intervention failed.');
  });

  it('surfaces formatAdminApiError on create-alert POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(
      httpErr(403, 'Missing scope admin.economy.alerts'),
    );

    render(<EconomyDashboard />);
    await waitFor(() => expect(screen.getByLabelText(/^Station$/)).toBeTruthy());

    await user.selectOptions(screen.getByLabelText(/^Station$/), 'st1');
    await user.selectOptions(screen.getByLabelText(/^Commodity$/), 'ore');
    await user.type(screen.getByLabelText(/Threshold Value/i), '15');
    await user.click(screen.getByRole('button', { name: /Create Alert/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/economy/create-alert',
        expect.objectContaining({ station_id: 'st1', commodity: 'ore' }),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.economy.alerts');
    expect(toastError).not.toHaveBeenCalledWith('Failed to create price alert');
  });

  it('surfaces rate-limit copy on create-alert POST 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(httpErr(429));

    render(<EconomyDashboard />);
    await waitFor(() => expect(screen.getByLabelText(/^Station$/)).toBeTruthy());

    await user.selectOptions(screen.getByLabelText(/^Station$/), 'st1');
    await user.selectOptions(screen.getByLabelText(/^Commodity$/), 'ore');
    await user.type(screen.getByLabelText(/Threshold Value/i), '15');
    await user.click(screen.getByRole('button', { name: /Create Alert/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Failed to create price alert');
  });

  it('surfaces formatAdminApiError on delete-alert DELETE 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { alert_id: 'alert-1' },
      status: 200,
    });
    vi.mocked(api.delete).mockRejectedValue(
      httpErr(403, 'Missing scope admin.economy.alerts'),
    );

    render(<EconomyDashboard />);
    await waitFor(() => expect(screen.getByLabelText(/^Station$/)).toBeTruthy());

    await user.selectOptions(screen.getByLabelText(/^Station$/), 'st1');
    await user.selectOptions(screen.getByLabelText(/^Commodity$/), 'ore');
    await user.type(screen.getByLabelText(/Threshold Value/i), '15');
    await user.click(screen.getByRole('button', { name: /Create Alert/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Delete/i })).toBeTruthy();
    });

    await user.click(screen.getByRole('button', { name: /Delete/i }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/economy/alerts/alert-1');
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.economy.alerts');
    expect(toastError).not.toHaveBeenCalledWith('Failed to delete price alert');
  });

  it('surfaces rate-limit copy on delete-alert DELETE 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { alert_id: 'alert-1' },
      status: 200,
    });
    vi.mocked(api.delete).mockRejectedValue(httpErr(429));

    render(<EconomyDashboard />);
    await waitFor(() => expect(screen.getByLabelText(/^Station$/)).toBeTruthy());

    await user.selectOptions(screen.getByLabelText(/^Station$/), 'st1');
    await user.selectOptions(screen.getByLabelText(/^Commodity$/), 'ore');
    await user.type(screen.getByLabelText(/Threshold Value/i), '15');
    await user.click(screen.getByRole('button', { name: /Create Alert/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Delete/i })).toBeTruthy();
    });

    await user.click(screen.getByRole('button', { name: /Delete/i }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/economy/alerts/alert-1');
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Failed to delete price alert');
  });
});
