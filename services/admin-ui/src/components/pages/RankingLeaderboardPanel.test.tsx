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
});
