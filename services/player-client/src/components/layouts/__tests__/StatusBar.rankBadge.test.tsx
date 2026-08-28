// @vitest-environment jsdom
/**
 * StatusBar — compact rank insignia badge (LEG-2606 / ranking.md:230).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'commander' }, logout: vi.fn() }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ linkStatus: 'up' }),
}));

const mockGetRank = vi.fn();
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

let mockPlayerState: Record<string, unknown> | null = null;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentShip: null,
    isLoading: false,
    refreshPlayerState: vi.fn(),
  }),
}));

vi.mock('../../../contexts/SettingsContext', () => ({
  useSettings: () => ({ uiScale: 1, setUiScale: vi.fn() }),
}));

import StatusBar from '../StatusBar';

const basePlayer = {
  credits: 1000,
  max_turns: 1000,
  turns: 500,
  attack_drones: 0,
  defense_drones: 0,
  mines: 0,
  name_color: '#00D9FF',
  military_rank: 'Recruit',
  personal_reputation: 0,
  reputation_tier: 'Neutral',
};

describe('StatusBar — rank insignia badge', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockPlayerState = { ...basePlayer };
    mockGetRank.mockReset();
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
        </MemoryRouter>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  };

  it('renders rank level when rankingAPI.getRank succeeds', async () => {
    mockGetRank.mockResolvedValue({
      rank_level: 7,
      rank_tier: 'NCO',
      current_rank: 'Petty Officer',
    });

    await renderBar();

    const badge = container.querySelector('.rank-badge--compact');
    expect(badge).not.toBeNull();
    expect(badge?.querySelector('.rank-level')?.textContent).toBe('7');
    expect(container.querySelector('.repb')).not.toBeNull();
  });

  it('shows em dash when rankingAPI.getRank rejects', async () => {
    mockGetRank.mockRejectedValue(new Error('network'));

    await renderBar();

    const badge = container.querySelector('.rank-badge--compact');
    expect(badge).not.toBeNull();
    expect(badge?.querySelector('.rank-level')?.textContent).toBe('—');
  });
});
