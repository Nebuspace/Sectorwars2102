// @vitest-environment jsdom
/**
 * RankDisplay — WO-UIPC-COCKPITINSTRUMENT-OCCLUSION follow-up hardening.
 *
 * `RankInfo`'s shape (incl. `bonuses`) is enforced by the TS type, not at
 * runtime -- discovered live while proving the CockpitInstrument occlusion
 * fix: a 200 response missing `bonuses` crashed the whole SERVICE RECORD
 * panel (`rankInfo.bonuses.trading_discount_percent` on undefined). Mirrors
 * ReputationPage.test.tsx's seam (jsdom + react-dom/client createRoot +
 * act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetRank = vi.fn();

vi.mock('../../services/api', () => ({
  rankingAPI: {
    getRank: (...a: unknown[]) => mockGetRank(...a),
  },
}));

import RankDisplay from './RankDisplay';

const FULL_RANK = {
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
  bonuses: {
    trading_discount_percent: 5,
    max_turns_bonus: 10,
    combat_damage_bonus_percent: 3,
  },
  is_max_rank: false,
};

describe('RankDisplay', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetRank.mockReset();
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
      root.render(<RankDisplay />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders rank, tier, and bonuses on a well-formed response', async () => {
    mockGetRank.mockResolvedValue(FULL_RANK);
    await mount();

    expect(container.querySelector('.rank-name')?.textContent).toBe('Commander');
    expect(container.querySelector('.rank-tier')?.textContent).toBe('Officer');
    expect(container.querySelector('.bonus-value')?.textContent).toBe('-5%');
  });

  it('does not crash and hides the bonus row when `bonuses` is missing from the response', async () => {
    const { bonuses, ...withoutBonuses } = FULL_RANK;
    mockGetRank.mockResolvedValue(withoutBonuses);

    await expect(mount()).resolves.not.toThrow();

    expect(container.querySelector('.rank-error')).toBeNull();
    expect(container.querySelector('.rank-name')?.textContent).toBe('Commander');
    // No bonus data -> no bonus-item rows, but the rest of the panel renders.
    expect(container.querySelectorAll('.bonus-item').length).toBe(0);
  });

  it('does not crash on a fully malformed (empty object) response', async () => {
    mockGetRank.mockResolvedValue({});

    await expect(mount()).resolves.not.toThrow();
    expect(container.querySelector('.rank-error')).toBeNull();
    expect(container.querySelectorAll('.bonus-item').length).toBe(0);
  });

  it('shows the error state instead of crashing when the fetch rejects', async () => {
    mockGetRank.mockRejectedValue(new Error('Network down'));
    await mount();

    expect(container.querySelector('.rank-error')?.textContent).toBe('Network down');
  });
});
