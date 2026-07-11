// @vitest-environment jsdom
/**
 * RankProgress — WO-UIPC-COCKPITINSTRUMENT-OCCLUSION follow-up hardening.
 *
 * `RankProgressData`'s shape is enforced by the TS type, not at runtime --
 * discovered live while proving the CockpitInstrument occlusion fix: a 200
 * response missing `progress_percent`/`requirements`/`stats` crashed the
 * whole SERVICE RECORD panel (`data.progress_percent.toFixed`,
 * `data.requirements.map`, `data.stats.combat_victories`, all on
 * undefined). Mirrors ReputationPage.test.tsx's seam (jsdom +
 * react-dom/client createRoot + act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetProgress = vi.fn();

vi.mock('../../services/api', () => ({
  rankingAPI: {
    getProgress: (...a: unknown[]) => mockGetProgress(...a),
  },
}));

import RankProgress from './RankProgress';

const FULL_PROGRESS = {
  player_id: 'p1',
  username: 'TESTPILOT',
  current_rank: 'Commander',
  rank_level: 5,
  rank_tier: 'Officer',
  rank_points: 4200,
  points_to_next_rank: 800,
  next_rank: 'Captain',
  next_rank_points_required: 5000,
  progress_percent: 84,
  is_max_rank: false,
  stats: {
    combat_victories: 12,
    total_trades: 340,
    trade_volume: 1500000,
    exploration_score: 88,
    credits: 125000,
    turns_remaining: 480,
  },
  requirements: [
    { name: 'Combat Wins', current: 12, required: 20, met: false },
  ],
};

describe('RankProgress', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetProgress.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<RankProgress />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders progress %, requirements, and stats on a well-formed response', async () => {
    mockGetProgress.mockResolvedValue(FULL_PROGRESS);
    await mount();

    expect(container.querySelector('.rank-progress-pct')?.textContent).toBe('84.0%');
    expect(container.querySelectorAll('.req-item').length).toBe(1);
    expect(container.querySelector('.stats-grid')?.textContent).toContain('12');
  });

  it('does not crash on a fully malformed (empty object) response', async () => {
    mockGetProgress.mockResolvedValue({ current_rank: 'Commander', rank_tier: 'Officer', is_max_rank: false });

    await expect(mount()).resolves.not.toThrow();

    expect(container.querySelector('.rank-progress-error')).toBeNull();
    // Missing progress_percent -> defaults to 0, still renders a number.
    expect(container.querySelector('.rank-progress-pct')?.textContent).toBe('0.0%');
    // Missing requirements/stats -> empty list / zeroed stat grid, no crash.
    expect(container.querySelectorAll('.req-item').length).toBe(0);
    expect(container.querySelector('.stats-grid')).not.toBeNull();
  });

  it('shows the error state instead of crashing when the fetch rejects', async () => {
    mockGetProgress.mockRejectedValue(new Error('Network down'));
    await mount();

    expect(container.querySelector('.rank-progress-error')?.textContent).toBe('Network down');
  });
});
