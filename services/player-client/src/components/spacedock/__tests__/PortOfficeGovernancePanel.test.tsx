// @vitest-environment jsdom
/**
 * PortOfficeGovernancePanel — LEG-4121 station governance vote wire.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getSyndicateStatus = vi.fn();
const castGovernanceVote = vi.fn();

let mockPlayerState: { id?: string } | null = { id: 'owner-1' };

vi.mock('../../../services/api', () => ({
  portOwnershipAPI: {
    getSyndicateStatus: (...args: unknown[]) => getSyndicateStatus(...args),
    castGovernanceVote: (...args: unknown[]) => castGovernanceVote(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
  }),
}));

import PortOfficeGovernancePanel, {
  formatGovernanceVoteError,
} from '../PortOfficeGovernancePanel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const syndicateFixture = {
  station_id: 'st-1',
  owner_id: 'owner-1',
  mode: 'syndicate',
  shares: [
    { player_id: 'owner-1', pct: 60 },
    { player_id: 'other-2', pct: 40 },
  ],
  pending_invites: [],
  is_primary: true,
};

describe('formatGovernanceVoteError (LEG-4121)', () => {
  const fallback = 'Governance vote failed. Please try again.';

  it('densifies TypeError without transport strings', () => {
    expect(formatGovernanceVoteError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatGovernanceVoteError(new TypeError('Failed to fetch'), fallback)).not.toMatch(
      /TypeError/i,
    );
  });

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatGovernanceVoteError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatGovernanceVoteError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(formatGovernanceVoteError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatGovernanceVoteError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});

describe('PortOfficeGovernancePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getSyndicateStatus.mockReset();
    castGovernanceVote.mockReset();
    mockPlayerState = { id: 'owner-1' };
    getSyndicateStatus.mockResolvedValue(syndicateFixture);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('casts tariff vote with tip body fields and surfaces tip status', async () => {
    castGovernanceVote.mockResolvedValue({
      id: 'vote-1',
      station_id: 'st-1',
      vote_type: 'tariff',
      status: 'open',
      window_ends_at: '2099-01-01T00:00:00Z',
      ballots: [{ player_id: 'owner-1', position: 'for' }],
      resolution: { yes_weight: 0.6, threshold: 0.5, status: 'open', passed: false },
    });

    await act(async () => {
      root.render(<PortOfficeGovernancePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    expect(container.querySelector('[data-testid="po-governance-form"]')).toBeTruthy();

    const castBtn = container.querySelector(
      '[data-testid="po-governance-cast"]',
    ) as HTMLButtonElement;
    await act(async () => {
      castBtn.click();
      await flush();
    });

    expect(castGovernanceVote).toHaveBeenCalledWith('st-1', {
      vote_type: 'tariff',
      proposed_value: { tax_rate: 0.1 },
      voter_stake_pct: 60,
      position: 'for',
    });

    const text = container.querySelector('[data-testid="po-governance-msg"]')?.textContent ?? '';
    expect(text).toMatch(/Status: open/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('surfaces 403 cast errors with player-safe copy', async () => {
    castGovernanceVote.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<PortOfficeGovernancePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const castBtn = container.querySelector(
      '[data-testid="po-governance-cast"]',
    ) as HTMLButtonElement;
    await act(async () => {
      castBtn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="po-governance-msg"]')?.textContent;
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('surfaces 429 cast errors with player-safe copy', async () => {
    castGovernanceVote.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(<PortOfficeGovernancePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const castBtn = container.querySelector(
      '[data-testid="po-governance-cast"]',
    ) as HTMLButtonElement;
    await act(async () => {
      castBtn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="po-governance-msg"]')?.textContent;
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
  });

  it('hides panel when not syndicate mode', async () => {
    getSyndicateStatus.mockResolvedValue({
      ...syndicateFixture,
      mode: 'solo',
      shares: [{ player_id: 'owner-1', pct: 100 }],
    });

    await act(async () => {
      root.render(<PortOfficeGovernancePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    expect(container.querySelector('[data-testid="po-governance-form"]')).toBeNull();
  });
});
