import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerAnalytics from './PlayerAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('./RankingLeaderboardPanel', () => ({
  default: () => <div data-testid="ranking-stub" />,
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

function mockApis({
  retention,
  analyticsFail = false,
}: {
  retention?: number | null;
  analyticsFail?: boolean;
}) {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
      return Promise.resolve({
        data: {
          players: [],
          total_count: 0,
        },
      });
    }
    if (url === '/api/v1/admin/analytics/real-time') {
      if (analyticsFail) {
        return Promise.reject(new Error('analytics down'));
      }
      const payload: Record<string, unknown> = {
        total_active_players: 10,
        total_credits_circulation: 1000,
        new_players_today: 2,
        players_online_now: 3,
        total_players: 50,
        suspicious_activity_alerts: 0,
      };
      if (retention !== undefined && retention !== null) {
        payload.player_retention_rate_7d = retention;
      }
      // omit field entirely when retention === null → asCount → null
      return Promise.resolve({ data: { data: payload } });
    }
    if (url === '/api/v1/admin/regions') {
      return Promise.resolve({ data: { regions: [] } });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

describe('PlayerAnalytics retention rate card (LEG-376)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows 7d retention rate when analytics returns player_retention_rate_7d', async () => {
    mockApis({ retention: 72.5 });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-retention-rate'));
    expect(card.textContent).toContain('72.5%');
    expect(card.textContent).toMatch(/7-day retention/i);
    expect(card.className).not.toContain('stat-not-tracked');
    expect(card.textContent).not.toMatch(/No retention telemetry surfaced yet/);
  });

  it('demotes to em-dash when retention field is absent', async () => {
    mockApis({ retention: null });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-retention-rate'));
    expect(card.querySelector('.dashboard-stat-value')?.textContent).toMatch(/—|–|-/);
    expect(card.className).toContain('stat-not-tracked');
    expect(card.textContent).toMatch(/Retention rate unavailable/i);
  });

  it('demotes when analytics endpoint is unavailable', async () => {
    mockApis({ retention: 72.5, analyticsFail: true });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-retention-rate'));
    expect(card.querySelector('.dashboard-stat-value')?.textContent).toMatch(/—|–|-/);
    expect(card.className).toContain('stat-not-tracked');
    expect(card.textContent).toMatch(/Analytics endpoint unavailable/i);
  });
});
