// @vitest-environment jsdom
/**
 * ElectionCard — regional-governance ballot (WO-REGOV-VOTE-UI).
 *
 * governanceAPI is mocked at the module boundary (not fetch), mirroring
 * RegionTradeDockPanel.test.tsx's house pattern: jsdom + react-dom/client
 * createRoot + act(), no RTL. Pins:
 *  - the ACTIVE ballot renders every candidate from the election's own
 *    candidates list (no separate fetch)
 *  - CAST VOTE only ARMS the finality confirm on first click — the vote
 *    request never fires until CONFIRM VOTE is clicked
 *  - a bare 409 ERR_ALREADY_VOTED response locks the ballot into a terminal
 *    "vote recorded" state instead of leaving it retryable/broken
 *  - the PENDING self-nominate CTA disappears once the current player is
 *    already a registered candidate
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Election } from '../../../types/governance';

const mockCastElectionVote = vi.fn();
const mockRegisterCandidacy = vi.fn();

vi.mock('../../../services/api', () => ({
  governanceAPI: {
    castElectionVote: (...a: unknown[]) => mockCastElectionVote(...a),
    registerCandidacy: (...a: unknown[]) => mockRegisterCandidacy(...a),
  },
}));

import ElectionCard from '../ElectionCard';

const ACTIVE_ELECTION: Election = {
  id: 'election-1',
  position: 'governor',
  candidates: [
    { player_id: 'player-a', platform: 'Lower taxes' },
    { player_id: 'player-b' },
  ],
  voting_opens_at: new Date(Date.now() - 3600_000).toISOString(),
  voting_closes_at: new Date(Date.now() + 3600_000).toISOString(),
  results: null,
  status: 'active',
};

describe('ElectionCard', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockCastElectionVote.mockReset();
    mockRegisterCandidacy.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  it('renders every candidate from the election object on an ACTIVE ballot', async () => {
    await act(async () => {
      root.render(
        <ElectionCard
          election={ACTIVE_ELECTION}
          regionId="region-1"
          currentPlayerId="player-c"
          canVote={true}
          isCitizen={true}
          onChanged={() => {}}
        />
      );
    });

    const options = container.querySelectorAll('.gov-ballot-option');
    expect(options).toHaveLength(2);
    expect(container.textContent).toContain('Lower taxes');
  });

  it('blocks the vote request until the finality confirm is clicked — first click only arms it', async () => {
    await act(async () => {
      root.render(
        <ElectionCard
          election={ACTIVE_ELECTION}
          regionId="region-1"
          currentPlayerId="player-c"
          canVote={true}
          isCitizen={true}
          onChanged={() => {}}
        />
      );
    });

    // React wires a controlled checkable input's onChange to the native
    // 'click' event (a long-standing React/jsdom quirk) — a bare 'change'
    // dispatch never reaches the handler, so this must be a click.
    const radio = container.querySelector('input[type="radio"]') as HTMLInputElement;
    await act(async () => {
      radio.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const castBtn = container.querySelector('.gov-btn.primary') as HTMLButtonElement;
    await act(async () => {
      castBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // Confirm card must now be showing, and no request has fired yet.
    expect(mockCastElectionVote).not.toHaveBeenCalled();
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    expect(confirmBtn).not.toBeNull();
    expect(confirmBtn.textContent).toContain('CONFIRM VOTE');
    expect(container.textContent).toContain('Your vote is recorded. Votes are final once cast.');

    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockCastElectionVote).toHaveBeenCalledTimes(1);
    expect(mockCastElectionVote).toHaveBeenCalledWith('region-1', 'election-1', 'player-a');
  });

  it('locks into a terminal "vote recorded" state on a bare 409 ERR_ALREADY_VOTED rejection', async () => {
    mockCastElectionVote.mockRejectedValue(new Error('ERR_ALREADY_VOTED'));

    await act(async () => {
      root.render(
        <ElectionCard
          election={ACTIVE_ELECTION}
          regionId="region-1"
          currentPlayerId="player-c"
          canVote={true}
          isCitizen={true}
          onChanged={() => {}}
        />
      );
    });

    const radio = container.querySelector('input[type="radio"]') as HTMLInputElement;
    await act(async () => {
      radio.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const castBtn = container.querySelector('.gov-btn.primary') as HTMLButtonElement;
    await act(async () => { castBtn.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    const confirmBtn = container.querySelector('.gov-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    // The ballot is gone; a terminal recorded-vote note replaced it, and
    // there is no leftover retryable CAST VOTE button.
    expect(container.querySelector('.gov-ballot-list')).toBeNull();
    expect(container.textContent).toContain('VOTE RECORDED');
  });

  it('hides the self-nominate CTA once the current player is already a registered candidate', async () => {
    const pendingElection: Election = {
      ...ACTIVE_ELECTION,
      status: 'pending',
      candidates: [{ player_id: 'player-c' }],
    };

    await act(async () => {
      root.render(
        <ElectionCard
          election={pendingElection}
          regionId="region-1"
          currentPlayerId="player-c"
          canVote={true}
          isCitizen={true}
          onChanged={() => {}}
        />
      );
    });

    expect(container.querySelector('.gov-nominate-form')).toBeNull();
    expect(container.textContent).toContain('You are registered as a candidate.');
  });
});
