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

import RankDisplay, { formatRankDisplayLoadError } from './RankDisplay';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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
  is_wanted: false,
  is_suspect: false,
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

  it('shows Wanted law status when is_wanted is true (LEG-4127)', async () => {
    mockGetRank.mockResolvedValue({ ...FULL_RANK, is_wanted: true, is_suspect: true });
    await mount();

    const badge = container.querySelector('[data-testid="rank-law-status"]');
    expect(badge?.textContent).toBe('Wanted');
    expect(badge?.classList.contains('wanted')).toBe(true);
    expect(container.querySelector('.rank-username.wanted')?.textContent).toBe('TESTPILOT');
    // Wanted overrides Suspect — no Suspect chip when both flags true.
    expect(container.textContent).not.toMatch(/Suspect/);
  });

  it('shows Suspect law status when is_suspect is true and is_wanted is false (LEG-4127)', async () => {
    mockGetRank.mockResolvedValue({ ...FULL_RANK, is_wanted: false, is_suspect: true });
    await mount();

    const badge = container.querySelector('[data-testid="rank-law-status"]');
    expect(badge?.textContent).toBe('Suspect');
    expect(badge?.classList.contains('suspect')).toBe(true);
    expect(container.querySelector('.rank-username.suspect')?.textContent).toBe('TESTPILOT');
  });

  it('hides law status when is_wanted and is_suspect are false (LEG-4127)', async () => {
    mockGetRank.mockResolvedValue(FULL_RANK);
    await mount();

    expect(container.querySelector('[data-testid="rank-law-status"]')).toBeNull();
    expect(container.querySelector('.rank-username.wanted')).toBeNull();
    expect(container.querySelector('.rank-username.suspect')).toBeNull();
    expect(container.querySelector('.rank-username')?.textContent).toBe('TESTPILOT');
  });

  it('surfaces 404 server detail from rank load refusal', async () => {
    mockGetRank.mockRejectedValue(
      apiRequestError(404, 'Rank information not found'),
    );
    await mount();

    expect(container.querySelector('.rank-error')?.textContent).toBe(
      'Rank information not found',
    );
  });

  it('formatRankDisplayLoadError hides bare API Error status codes', () => {
    expect(formatRankDisplayLoadError(apiRequestError(404))).toBe(
      'Failed to load rank info',
    );
  });

  it('formatRankDisplayLoadError falls back on TypeError network collapse (LEG-3017)', () => {
    const text = formatRankDisplayLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load rank info/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatRankDisplayLoadError falls back on axios Network Error / Failed to fetch (LEG-3342)', () => {
    expect(formatRankDisplayLoadError(new Error('Network Error'))).toBe('Failed to load rank info');
    expect(formatRankDisplayLoadError(new Error('Failed to fetch'))).toBe('Failed to load rank info');
    expect(formatRankDisplayLoadError(new Error('Network Error'))).not.toBe('Network Error');
  });
});
