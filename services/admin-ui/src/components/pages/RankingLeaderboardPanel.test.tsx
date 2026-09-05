import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RankingLeaderboardPanel from './RankingLeaderboardPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

describe('RankingLeaderboardPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('loads GET /api/v1/ranking/leaderboard and renders rows', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        total_players: 42,
        entries: [
          {
            position: 1,
            player_id: 'p1',
            username: 'Ace',
            military_rank: 'Captain',
            rank_points: 900,
            rank_level: 5,
            rank_tier: 'Officer',
            is_wanted: true,
          },
        ],
      },
    });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByTestId('admin-ranking-row-p1')).toBeTruthy();
    });
    expect(api.get).toHaveBeenCalledWith('/api/v1/ranking/leaderboard', {
      params: { limit: 20 },
    });
    expect(screen.getByTestId('admin-ranking-total').textContent).toContain('42');
    expect(screen.getByText('Ace')).toBeTruthy();
    expect(screen.getByText(/wanted/)).toBeTruthy();
  });

  it('renders rank insignia badge with tier color when rank_tier is present', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        total_players: 1,
        entries: [
          {
            position: 1,
            player_id: 'p2',
            username: 'Bravo',
            military_rank: 'Lieutenant',
            rank_points: 500,
            rank_level: 3,
            rank_tier: 'Officer',
          },
        ],
      },
    });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByTestId('admin-rank-badge-p2')).toBeTruthy();
    });
    const badge = screen.getByTestId('admin-rank-badge-p2');
    expect(badge.textContent).toBe('3');
    expect((badge as HTMLElement).style.borderColor).toBe('rgb(255, 68, 255)');
    expect(screen.getByText('Lieutenant')).toBeTruthy();
  });

  it('renders rank row gracefully when rank_tier is missing', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        total_players: 1,
        entries: [
          {
            position: 1,
            player_id: 'p3',
            username: 'Charlie',
            military_rank: 'Private',
            rank_points: 100,
            rank_level: 1,
          },
        ],
      },
    });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByTestId('admin-rank-badge-p3')).toBeTruthy();
    });
    const badge = screen.getByTestId('admin-rank-badge-p3');
    expect(badge.textContent).toBe('1');
    expect((badge as HTMLElement).style.borderColor).toBe('rgb(136, 136, 136)');
    expect(screen.getByText('Private')).toBeTruthy();
  });

  it('shows error when the request fails', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 403, data: { detail: 'Missing scope admin.players.view' } },
    });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('admin.players.view');
    });
  });

  it('shows rate-limit copy on 429', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 429 },
    });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });
  it('reports a 403 as PLAYERS_VIEW scope, not bare Forbidden detail', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/PLAYERS_VIEW/);
    });
  });

  it('surfaces honest fallback on load TypeError/network collapse (LEG-3019)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load leaderboard/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('reports a 429 as an admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('surfaces honest fallback on load TypeError/network collapse (LEG-3006)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load leaderboard/i);
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});

describe('RankingLeaderboardPanel axios Network Error densify (LEG-3533)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on leaderboard load', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<RankingLeaderboardPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load leaderboard/i);
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load leaderboard/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });
});
