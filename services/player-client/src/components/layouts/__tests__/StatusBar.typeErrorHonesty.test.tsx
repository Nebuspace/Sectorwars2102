// @vitest-environment jsdom
/**
 * LEG-3782 Soft-ORDER — StatusBar rank load TypeError/Network Error densify.
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

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

describe('StatusBar rank load typeErrorHonesty densify (LEG-3782)', () => {
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

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('hides raw transport text when getRank rejects with %s', async (_label, err) => {
    mockGetRank.mockRejectedValue(err);

    await renderBar();

    assertNoTransportLeak(container.textContent ?? '');

    const badge = container.querySelector('.rank-badge--compact');
    expect(badge).not.toBeNull();
    expect(badge?.querySelector('.rank-level')?.textContent).toBe('—');
  });
});
