import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerAnalytics from './PlayerAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('./RankingLeaderboardPanel', () => ({
  default: () => <div data-testid="ranking-stub" />,
}));

vi.mock('./ReEngagementQueuePanel', () => ({
  default: () => <div data-testid="re-engagement-stub" />,
}));

vi.mock('./components/PlayerSearchAndFilter', () => ({
  default: () => <div data-testid="filters-stub" />,
}));

vi.mock('../admin/PlayerDetailEditor', () => ({
  default: () => null,
}));

vi.mock('../admin/BulkOperationPanel', () => ({
  default: () => null,
}));

vi.mock('../admin/PlayerAssetManager', () => ({
  default: () => null,
}));

vi.mock('../admin/EmergencyOperationsPanel', () => ({
  default: () => null,
}));

vi.mock('../admin/PlayerBountyPanel', () => ({
  default: () => null,
}));

function mockSuccessfulCompanionGets() {
  return (url: string) => {
    if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
      return Promise.resolve({
        data: { players: [], total_count: 0 },
      });
    }
    if (url === '/api/v1/admin/analytics/real-time') {
      return Promise.resolve({
        data: {
          data: {
            total_active_players: 10,
            total_credits_circulation: 1000,
            new_players_today: 2,
            players_online_now: 3,
            total_players: 50,
            suspicious_activity_alerts: 0,
          },
        },
      });
    }
    if (url === '/api/v1/admin/regions') {
      return Promise.resolve({ data: { regions: [] } });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  };
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
 * LEG-3656 Soft-ORDER — PlayerAnalytics TypeError/Network Error honesty densify.
 * LEG-3905 Soft-ORDER — 403/429 HTTP honesty densify (adminHttpErrorMessage).
 */
describe('PlayerAnalytics typeErrorHonesty densify (LEG-3656)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on comprehensive fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(new Error('Network Error'));
      }
      return mockSuccessfulCompanionGets()(url);
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load player data/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on comprehensive fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return mockSuccessfulCompanionGets()(url);
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load player data/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on regions fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/regions') {
        return Promise.reject(new Error('Network Error'));
      }
      return mockSuccessfulCompanionGets()(url);
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load regions/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on regions fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/regions') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return mockSuccessfulCompanionGets()(url);
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load regions/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with PLAYERS_VIEW scope copy when comprehensive GET is denied', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(axiosError(403));
      }
      return mockSuccessfulCompanionGets()(url);
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied|PLAYERS_VIEW/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied/i);
    expect(text).toMatch(/PLAYERS_VIEW/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on comprehensive GET', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(axiosError(429));
      }
      return mockSuccessfulCompanionGets()(url);
    });

    render(<PlayerAnalytics />);

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
