// @vitest-environment jsdom
/**
 * Leaderboard — load/error/empty, category switch, current-player highlight.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getPublicLeaderboard = vi.fn();

vi.mock('../../../services/api', () => ({
  rankingAPI: {
    getPublicLeaderboard: (...args: unknown[]) => getPublicLeaderboard(...args),
  },
}));

import Leaderboard, { formatLeaderboardLoadError } from '../Leaderboard';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('Leaderboard', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('shows loading then ranks, highlighting the current player', async () => {
    getPublicLeaderboard.mockResolvedValue({
      category: 'rank_points',
      total_players: 42,
      player_position: 2,
      entries: [
        {
          position: 1,
          player_id: 'p-other',
          nickname: 'Nova',
          military_rank: 'Captain',
          score: 9001,
        },
        {
          position: 2,
          player_id: 'p-me',
          nickname: 'Ada',
          military_rank: 'Lieutenant',
          score: 1200,
        },
      ],
    });

    await act(async () => {
      root.render(<Leaderboard playerId="p-me" />);
    });

    expect(getPublicLeaderboard).toHaveBeenCalledWith('rank_points', 20);
    expect(container.textContent).toContain('42 players');
    expect(container.textContent).toContain('Nova');
    expect(container.textContent).toContain('Ada');
    expect(container.querySelector('tr.current-player')?.textContent).toContain('Ada');
    expect(container.textContent).toContain('Points');
  });

  it('surfaces fetch errors', async () => {
    getPublicLeaderboard.mockRejectedValue(new Error('ranking offline'));
    await act(async () => {
      root.render(<Leaderboard />);
    });
    expect(container.textContent).toContain('ranking offline');
  });

  it('surfaces 400 invalid category with server detail on load failure', async () => {
    getPublicLeaderboard.mockRejectedValue(
      apiRequestError(
        400,
        "Invalid category 'bogus'. Must be one of: combat, exploration, rank_points, trading",
      ),
    );
    await act(async () => {
      root.render(<Leaderboard />);
    });
    expect(container.textContent).toContain(
      "Invalid category 'bogus'. Must be one of: combat, exploration, rank_points, trading",
    );
  });

  it('refetches when switching categories', async () => {
    getPublicLeaderboard
      .mockResolvedValueOnce({
        category: 'rank_points',
        total_players: 1,
        player_position: null,
        entries: [],
      })
      .mockResolvedValueOnce({
        category: 'combat',
        total_players: 1,
        player_position: null,
        entries: [
          {
            position: 1,
            player_id: 'p1',
            nickname: 'Ace',
            military_rank: 'Ensign',
            score: 3,
          },
        ],
      });

    await act(async () => {
      root.render(<Leaderboard />);
    });
    expect(container.textContent).toContain('No entries yet');

    const combatBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Combat'),
    ) as HTMLButtonElement;
    await act(async () => {
      combatBtn.click();
    });

    expect(getPublicLeaderboard).toHaveBeenLastCalledWith('combat', 20);
    expect(container.textContent).toContain('Ace');
    expect(container.textContent).toContain('Victories');
  });

  it('renders Medals column with pinned icon and count when API provides medal fields', async () => {
    getPublicLeaderboard.mockResolvedValue({
      category: 'rank_points',
      total_players: 1,
      player_position: 1,
      entries: [
        {
          position: 1,
          player_id: 'p1',
          nickname: 'Ace',
          military_rank: 'Captain',
          score: 500,
          pinned_medal_id: 'bronze_cluster',
          medal_count: 7,
        },
      ],
    });

    await act(async () => {
      root.render(<Leaderboard />);
    });

    expect(container.textContent).toContain('Medals');
    expect(container.querySelector('[data-testid="player-name-plate-medal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe(
      '7',
    );
  });

  it('renders compact rank insignia with tier color on rank_points when API returns rank_tier', async () => {
    getPublicLeaderboard.mockResolvedValue({
      category: 'rank_points',
      total_players: 1,
      player_position: 1,
      entries: [
        {
          position: 1,
          player_id: 'p1',
          nickname: 'Ace',
          military_rank: 'Petty Officer',
          score: 500,
          rank_level: 7,
          rank_tier: 'NCO',
        },
      ],
    });

    await act(async () => {
      root.render(<Leaderboard />);
    });

    const badge = container.querySelector('.rank-badge--compact');
    expect(badge).not.toBeNull();
    expect(badge?.querySelector('.rank-level')?.textContent).toBe('7');
    expect((badge as HTMLElement).style.borderColor).toBe('rgb(74, 158, 255)');
    expect(container.textContent).toContain('Petty Officer');
  });

  it('falls back to text-only rank when rank_tier fields are missing', async () => {
    getPublicLeaderboard.mockResolvedValue({
      category: 'rank_points',
      total_players: 1,
      player_position: 1,
      entries: [
        {
          position: 1,
          player_id: 'p1',
          nickname: 'Ace',
          military_rank: 'Captain',
          score: 500,
        },
      ],
    });

    await act(async () => {
      root.render(<Leaderboard />);
    });

    expect(container.querySelector('.rank-badge--compact')).toBeNull();
    expect(container.textContent).toContain('Captain');
  });

  it('omits medal count badge when API omits medal_count', async () => {
    getPublicLeaderboard.mockResolvedValue({
      category: 'rank_points',
      total_players: 1,
      player_position: 1,
      entries: [
        {
          position: 1,
          player_id: 'p1',
          nickname: 'Ace',
          military_rank: 'Captain',
          score: 500,
          pinned_medal_id: 'bronze_cluster',
        },
      ],
    });

    await act(async () => {
      root.render(<Leaderboard />);
    });

    expect(container.querySelector('[data-testid="player-name-plate-medal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="player-name-plate-count"]')).toBeNull();
  });

  it('formatLeaderboardLoadError falls back on TypeError network collapse (LEG-3016)', () => {
    const text = formatLeaderboardLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load leaderboard/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatLeaderboardLoadError falls back on axios Network Error / Failed to fetch (LEG-3340)', () => {
    expect(formatLeaderboardLoadError(new Error('Network Error'))).toBe('Failed to load leaderboard');
    expect(formatLeaderboardLoadError(new Error('Failed to fetch'))).toBe('Failed to load leaderboard');
    expect(formatLeaderboardLoadError(new Error('Network Error'))).not.toBe('Network Error');
  });

});
