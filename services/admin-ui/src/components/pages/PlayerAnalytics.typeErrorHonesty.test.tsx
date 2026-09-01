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

/**
 * LEG-3656 Soft-ORDER — PlayerAnalytics TypeError/Network Error honesty densify.
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
});
