// @vitest-environment jsdom
/**
 * StatusBar — compact rank insignia badge (LEG-2606 / ranking.md:230).
 * Reuses rankingAPI.getRank() + RankDisplay tier colors; graceful em-dash
 * when the rank fetch fails.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetRank = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'commander' } }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ linkStatus: 'up' }),
}));

vi.mock('../../../contexts/SettingsContext', () => ({
  useSettings: () => ({ uiScale: 1, setUiScale: vi.fn() }),
}));

let mockPlayerState: Record<string, unknown> | null = null;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentShip: null,
    isLoading: false,
    refreshPlayerState: vi.fn(),
  }),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    rankingAPI: {
      ...actual.rankingAPI,
      getRank: (...args: unknown[]) => mockGetRank(...args),
    },
  };
});

import StatusBar from '../StatusBar';

const basePlayer = {
  credits: 1000,
  max_turns: 1000,
  turns: 100,
  attack_drones: 0,
  defense_drones: 0,
  mines: 0,
  name_color: '#00D9FF',
  military_rank: 'Recruit',
  reputation_tier: 'Neutral',
  personal_reputation: 0,
};

describe('StatusBar — rank insignia badge', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetRank.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockPlayerState = { ...basePlayer };
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    mockPlayerState = null;
  });

  const renderBar = async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <StatusBar />
        </MemoryRouter>
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders rank_level in the compact badge when API returns rank info', async () => {
    mockGetRank.mockResolvedValue({
      rank_level: 7,
      rank_tier: 'Officer',
      current_rank: 'Captain',
    });
    await renderBar();

    const badge = container.querySelector('.rank-insignia .rank-level');
    expect(badge?.textContent).toBe('7');
    expect(
      (container.querySelector('.rank-insignia .rank-badge') as HTMLElement)?.style.borderColor
    ).toBeTruthy();
  });

  it('shows em-dash when rank fetch fails', async () => {
    mockGetRank.mockRejectedValue(new Error('network'));
    await renderBar();

    const badge = container.querySelector('.rank-insignia .rank-level');
    expect(badge?.textContent).toBe('—');
  });

  it('keeps dossier military_rank text unchanged (no duplicate removal)', async () => {
    mockGetRank.mockResolvedValue({
      rank_level: 3,
      rank_tier: 'NCO',
      current_rank: 'Sergeant',
    });
    await renderBar();

    const nameChip = container.querySelector('.sb-name-chip');
    await act(async () => {
      nameChip?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });

    const rankField = Array.from(container.querySelectorAll('.sb-identity-v')).find(
      (el) => el.previousElementSibling?.textContent === 'RANK'
    );
    expect(rankField?.textContent).toBe('Recruit');
    expect(container.querySelector('.rank-insignia .rank-level')?.textContent).toBe('3');
  });
});
