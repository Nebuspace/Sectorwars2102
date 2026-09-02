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

import RankProgress, { formatRankProgressLoadError } from './RankProgress';

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
  is_wanted: false,
  is_suspect: false,
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
    expect(container.querySelectorAll('.rank-progress-req-item').length).toBe(1);
    expect(container.querySelector('.stats-grid')?.textContent).toContain('12');
  });

  it('does not crash on a fully malformed (empty object) response', async () => {
    mockGetProgress.mockResolvedValue({ current_rank: 'Commander', rank_tier: 'Officer', is_max_rank: false });

    await expect(mount()).resolves.not.toThrow();

    expect(container.querySelector('.rank-progress-error')).toBeNull();
    // Missing progress_percent -> defaults to 0, still renders a number.
    expect(container.querySelector('.rank-progress-pct')?.textContent).toBe('0.0%');
    // Missing requirements/stats -> empty list / zeroed stat grid, no crash.
    expect(container.querySelectorAll('.rank-progress-req-item').length).toBe(0);
    expect(container.querySelector('.stats-grid')).not.toBeNull();
  });

  it('shows the error state instead of crashing when the fetch rejects', async () => {
    mockGetProgress.mockRejectedValue(new Error('Network down'));
    await mount();

    expect(container.querySelector('.rank-progress-error')?.textContent).toBe('Network down');
  });

  it('surfaces 404 server detail on rank progress load failure', async () => {
    const err = new Error('Rank information not found');
    (err as { status?: number }).status = 404;
    mockGetProgress.mockRejectedValue(err);
    await mount();

    expect(container.querySelector('.rank-progress-error')?.textContent).toBe(
      'Rank information not found',
    );
  });

  it('formatRankProgressLoadError falls back on bare 404 without server detail', () => {
    const err = new Error('API Error: 404');
    (err as { status?: number }).status = 404;
    expect(formatRankProgressLoadError(err)).toBe('Failed to load rank progress');
  });

  it('formatRankProgressLoadError falls back on TypeError network collapse (LEG-3020)', () => {
    const text = formatRankProgressLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load rank progress/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatRankProgressLoadError falls back on axios Network Error / Failed to fetch (LEG-3343)', () => {
    expect(formatRankProgressLoadError(new Error('Network Error'))).toBe('Failed to load rank progress');
    expect(formatRankProgressLoadError(new Error('Failed to fetch'))).toBe('Failed to load rank progress');
    expect(formatRankProgressLoadError(new Error('Network Error'))).not.toBe('Network Error');
  });

  it('surfaces honest load fallback when getProgress rejects with TypeError', async () => {
    mockGetProgress.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();

    const errorEl = container.querySelector('.rank-progress-error');
    expect(errorEl?.textContent).toMatch(/Failed to load rank progress/i);
    expect(errorEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errorEl?.textContent).not.toMatch(/TypeError/i);
  });

  it('renders compact rank insignia when rank_level and rank_tier are present', async () => {
    mockGetProgress.mockResolvedValue(FULL_PROGRESS);
    await mount();

    const badge = container.querySelector('.rank-badge--compact');
    expect(badge).not.toBeNull();
    expect(badge?.querySelector('.rank-level')?.textContent).toBe('5');
    expect(container.textContent).toContain('Commander');
  });

  it('omits compact insignia when rank_level is missing', async () => {
    mockGetProgress.mockResolvedValue({
      ...FULL_PROGRESS,
      rank_level: undefined,
    });
    await mount();

    expect(container.querySelector('.rank-badge--compact')).toBeNull();
    expect(container.textContent).toContain('Commander');
  });

  it('shows Wanted law status when is_wanted is true (LEG-4130)', async () => {
    mockGetProgress.mockResolvedValue({
      ...FULL_PROGRESS,
      is_wanted: true,
      is_suspect: true,
    });
    await mount();

    const badge = container.querySelector('[data-testid="rank-law-status"]');
    expect(badge?.textContent).toBe('Wanted');
    expect(badge?.classList.contains('wanted')).toBe(true);
    expect(container.querySelector('[data-testid="rank-progress-username"].wanted')?.textContent).toBe(
      'TESTPILOT',
    );
    // Wanted overrides Suspect — no Suspect chip when both flags true.
    expect(container.textContent).not.toMatch(/Suspect/);
  });

  it('shows Suspect law status when is_suspect is true and is_wanted is false (LEG-4130)', async () => {
    mockGetProgress.mockResolvedValue({
      ...FULL_PROGRESS,
      is_wanted: false,
      is_suspect: true,
    });
    await mount();

    const badge = container.querySelector('[data-testid="rank-law-status"]');
    expect(badge?.textContent).toBe('Suspect');
    expect(badge?.classList.contains('suspect')).toBe(true);
    expect(container.querySelector('[data-testid="rank-progress-username"].suspect')?.textContent).toBe(
      'TESTPILOT',
    );
  });

  it('hides law status when is_wanted and is_suspect are false (LEG-4130)', async () => {
    mockGetProgress.mockResolvedValue({
      ...FULL_PROGRESS,
      is_wanted: false,
      is_suspect: false,
    });
    await mount();

    expect(container.querySelector('[data-testid="rank-law-status"]')).toBeNull();
    expect(container.querySelector('[data-testid="rank-progress-username"].wanted')).toBeNull();
    expect(container.querySelector('[data-testid="rank-progress-username"].suspect')).toBeNull();
    expect(container.querySelector('[data-testid="rank-progress-username"]')?.textContent).toBe(
      'TESTPILOT',
    );
  });
});
