import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import AITradingDashboard from './AITradingDashboard';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    isConnected: false,
    subscribe: () => () => {},
  }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../ai/MarketPredictionInterface', () => ({
  MarketPredictionInterface: () => <div data-testid="market-predictions-stub" />,
}));

vi.mock('../ai/RouteOptimizationDisplay', () => ({
  RouteOptimizationDisplay: () => <div data-testid="route-optimization-stub" />,
}));

vi.mock('../ai/PlayerBehaviorAnalytics', () => ({
  PlayerBehaviorAnalytics: () => <div data-testid="behavior-analytics-stub" />,
}));

const okModels = { data: [] as unknown[] };
const okPredictions = { data: [] as unknown[] };
const okProfiles = { data: [] as unknown[] };
const okMetrics = {
  data: {
    totalPredictions: null,
    avgAccuracy: null,
    activeProfiles: 0,
    recommendationAcceptance: null,
    modelHealth: null,
    queuedJobs: null,
    processingRate: null,
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
 * LEG-3651 Soft-ORDER — AITradingDashboard TypeError/Network Error densify.
 * LEG-3898 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('AITradingDashboard typeErrorHonesty densify (LEG-3651)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collapses axios Network Error on models load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) {
        throw new Error('Network Error');
      }
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) return okProfiles;
      if (url.includes('/ai/metrics')) return okMetrics;
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load AI trading data/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on profiles load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) return okModels;
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) {
        throw new Error('Network Error');
      }
      if (url.includes('/ai/metrics')) return okMetrics;
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load AI trading data/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on metrics load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) return okModels;
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) return okProfiles;
      if (url.includes('/ai/metrics')) {
        throw new Error('Network Error');
      }
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load AI trading data/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on models load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) return okProfiles;
      if (url.includes('/ai/metrics')) return okMetrics;
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load AI trading data/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on profiles load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) return okModels;
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/ai/metrics')) return okMetrics;
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load AI trading data/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on metrics load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) return okModels;
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) return okProfiles;
      if (url.includes('/ai/metrics')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load AI trading data/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with admin.ai.view scope copy when models GET is denied', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) {
        throw axiosError(403);
      }
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) return okProfiles;
      if (url.includes('/ai/metrics')) return okMetrics;
      return { data: {} };
    });

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied|admin\.ai\.view/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on models GET', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/ai/models')) {
        throw axiosError(429);
      }
      if (url.includes('/ai/predictions/accuracy')) return okPredictions;
      if (url.includes('/ai/profiles')) return okProfiles;
      if (url.includes('/ai/metrics')) return okMetrics;
      return { data: {} };
    });

    render(<AITradingDashboard />);

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
