import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { CombatOverview } from './CombatOverview';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useCombatUpdates: () => undefined,
}));

vi.mock('../charts/CombatActivityChart', () => ({
  CombatActivityChart: () => null,
}));

vi.mock('../combat/CombatFeed', () => ({
  CombatFeed: () => null,
}));

vi.mock('../combat/DisputePanel', () => ({
  DisputePanel: () => null,
}));

vi.mock('../combat/DroneOperationsTab', () => ({
  default: () => null,
}));

vi.mock('../combat/BalanceAnalytics', () => ({
  default: () => null,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const emptyStats = {
  timestamp: null,
  active_combats: { total: 0, by_type: {}, needing_intervention: 0 },
  balance_summary: {
    score: 0,
    total_combats_24h: 0,
    outliers_count: 0,
    top_recommendation: '',
  },
  dispute_summary: {
    total_disputes: 0,
    by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
    critical_disputes: [],
  },
  recent_combats: [],
};

function alertText(): string {
  return document.querySelector('.alert-message')?.textContent ?? '';
}

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
 * LEG-3637 Soft-ORDER — CombatOverview TypeError/Network Error densify.
 * LEG-3863 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('CombatOverview typeErrorHonesty densify (LEG-3637)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on live combat feed without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) {
        throw new Error('Network Error');
      }
      if (url.includes('dashboard-summary')) return { data: emptyStats };
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) return { data: [] };
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/Combat feed unavailable/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/Combat feed unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on dashboard-summary without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) return { data: [] };
      if (url.includes('dashboard-summary')) {
        throw new Error('Network Error');
      }
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) return { data: [] };
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/Combat statistics unavailable/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/Combat statistics unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on disputes fetch without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) return { data: [] };
      if (url.includes('dashboard-summary')) return { data: emptyStats };
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) {
        throw new Error('Network Error');
      }
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/Combat disputes unavailable/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/Combat disputes unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on live combat feed without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('dashboard-summary')) return { data: emptyStats };
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) return { data: [] };
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/Combat feed unavailable/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/Combat feed unavailable/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on dashboard-summary without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) return { data: [] };
      if (url.includes('dashboard-summary')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) return { data: [] };
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/Combat statistics unavailable/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/Combat statistics unavailable/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with friendly scope copy when combat live GET is denied', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) {
        throw axiosError(403);
      }
      if (url.includes('dashboard-summary')) return { data: emptyStats };
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) return { data: [] };
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/Access denied|Combat feed/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/Access denied|combat/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on combat live GET', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/combat/live')) {
        throw axiosError(429);
      }
      if (url.includes('dashboard-summary')) return { data: emptyStats };
      if (url.includes('/combat/logs')) return { data: [] };
      if (url.includes('/combat/disputes')) return { data: [] };
      return { data: [] };
    });

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = alertText();
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
