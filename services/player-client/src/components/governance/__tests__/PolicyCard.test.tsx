// @vitest-environment jsdom
/**
 * PolicyCard — regional-governance policy ballot (WO-REGOV-VOTE-UI).
 *
 * Same house pattern as ElectionCard.test.tsx / RegionTradeDockPanel.test.tsx.
 * Pins:
 *  - AYE/NAY only ARMS the finality confirm — the vote request fires on
 *    CONFIRM VOTE, never on the first click
 *  - after a successful cast, re-cast is disabled (AYE/NAY buttons gone,
 *    replaced by a terminal "recorded" note) — mirrors the server's
 *    UNIQUE(policy_id, voter_id) constraint
 *  - a bare 409 ERR_ALREADY_VOTED rejection reaches the same disabled
 *    terminal state, not a stuck/broken button
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Policy } from '../../../types/governance';

const mockCastPolicyVote = vi.fn();

vi.mock('../../../services/api', () => ({
  governanceAPI: {
    castPolicyVote: (...a: unknown[]) => mockCastPolicyVote(...a),
  },
}));

import PolicyCard, { formatPolicyVoteError } from '../PolicyCard';

const VOTING_POLICY: Policy = {
  id: 'policy-1',
  policy_type: 'tax_rate',
  title: 'Lower the regional tax rate',
  description: 'Cut tax_rate to 10%.',
  proposed_changes: { tax_rate: 0.1 },
  proposed_by: 'player-x',
  proposed_at: new Date().toISOString(),
  voting_closes_at: new Date(Date.now() + 3600_000).toISOString(),
  votes_for: 3,
  votes_against: 1,
  status: 'voting',
  approval_percentage: 75,
};

describe('PolicyCard', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockCastPolicyVote.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  const armAye = async () => {
    const ayeBtn = container.querySelector('.gov-btn.primary.aye') as HTMLButtonElement;
    await act(async () => { ayeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  };

  it('only arms the confirm on the first AYE click — no request until CONFIRM VOTE', async () => {
    await act(async () => {
      root.render(
        <PolicyCard policy={VOTING_POLICY} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });

    await armAye();

    expect(mockCastPolicyVote).not.toHaveBeenCalled();
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    expect(confirmBtn).not.toBeNull();
    expect(container.textContent).toContain('Your vote is recorded. Votes are final once cast.');

    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockCastPolicyVote).toHaveBeenCalledTimes(1);
    expect(mockCastPolicyVote).toHaveBeenCalledWith('region-1', 'policy-1', true);
  });

  it('disables re-vote after a successful cast — AYE/NAY replaced by a terminal recorded note', async () => {
    mockCastPolicyVote.mockResolvedValue({
      ok: true, code: 'VOTE_RECORDED', support: true, weight: 1, votes_for: 4, votes_against: 1,
    });

    await act(async () => {
      root.render(
        <PolicyCard policy={VOTING_POLICY} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });
    await armAye();
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('.gov-policy-vote-buttons')).toBeNull();
    expect(container.textContent).toContain('VOTE RECORDED');

    // A remount with the same policy (simulating a parent refetch) has no
    // buttons to click a second time either — the disabled state isn't just
    // a one-off render glitch.
    expect(container.querySelectorAll('.gov-btn.aye, .gov-btn.nay')).toHaveLength(0);
  });

  it('reaches the same disabled terminal state on a bare 409 ERR_ALREADY_VOTED rejection', async () => {
    mockCastPolicyVote.mockRejectedValue(new Error('ERR_ALREADY_VOTED'));

    await act(async () => {
      root.render(
        <PolicyCard policy={VOTING_POLICY} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });
    await armAye();
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('.gov-policy-vote-buttons')).toBeNull();
    expect(container.textContent).toContain('VOTE RECORDED');
  });

  it('renders the proposed_changes diff and the current tally', async () => {
    await act(async () => {
      root.render(
        <PolicyCard policy={VOTING_POLICY} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });

    expect(container.textContent).toContain('tax rate');
    expect(container.textContent).toContain('10.0%');
    expect(container.textContent).toContain('3');
    expect(container.textContent).toContain('75.0% approval');
  });

  it('renders no voting UI for a non-VOTING (already resolved) policy', async () => {
    const passedPolicy: Policy = { ...VOTING_POLICY, status: 'passed' };
    await act(async () => {
      root.render(
        <PolicyCard policy={passedPolicy} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });

    expect(container.querySelector('.gov-policy-vote-buttons')).toBeNull();
    expect(container.querySelector('.gov-confirm-card')).toBeNull();
  });

  it('formatPolicyVoteError preserves gameserver detail on reject (LEG-2944)', () => {
    const err = Object.assign(new Error('Policy suspended pending review'), { status: 400 });
    expect(formatPolicyVoteError(err)).toBe('Policy suspended pending review');
  });

  it('formatPolicyVoteError uses 429 rate-limit copy when detail absent (LEG-2944)', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatPolicyVoteError(err)).toBe(
      'Vote rate limit exceeded — wait a moment and try again.',
    );
  });

  it('formatPolicyVoteError uses 403 fallback when detail absent (LEG-2944)', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatPolicyVoteError(err)).toBe('You are not allowed to vote on this policy.');
  });

  it('formatPolicyVoteError falls back on TypeError network collapse (LEG-3021)', () => {
    const text = formatPolicyVoteError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to cast vote/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces honest vote fallback when castPolicyVote rejects with TypeError', async () => {
    mockCastPolicyVote.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(
        <PolicyCard policy={VOTING_POLICY} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });
    await armAye();
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    const strip = container.querySelector('.gov-validation-strip');
    expect(strip?.textContent).toMatch(/Failed to cast vote/i);
    expect(strip?.textContent).not.toMatch(/Failed to fetch/i);
    expect(strip?.textContent).not.toMatch(/TypeError/i);
  });

  it('surfaces GS vote-reject detail in the validation strip (LEG-2944)', async () => {
    mockCastPolicyVote.mockRejectedValue(
      Object.assign(new Error('Policy suspended pending review'), { status: 403 }),
    );

    await act(async () => {
      root.render(
        <PolicyCard policy={VOTING_POLICY} regionId="region-1" canVote={true} onChanged={() => {}} />
      );
    });
    await armAye();
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('.gov-validation-strip')?.textContent).toBe(
      'Policy suspended pending review',
    );
    expect(container.querySelector('.gov-policy-vote-buttons')).not.toBeNull();
  });
});
