// @vitest-environment jsdom
/**
 * CitizenshipBadge — WO-WIRE-CLAIM-COLONY-CITIZENSHIP.
 * Pins Claim button → POST claimColonyCitizenship when owns_colony_in_region
 * and not yet citizen.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getMyMembership = vi.fn();
const claimColonyCitizenship = vi.fn();

vi.mock('../../../services/api', () => ({
  governanceAPI: {
    getMyMembership: (...args: unknown[]) => getMyMembership(...args),
    claimColonyCitizenship: (...args: unknown[]) => claimColonyCitizenship(...args),
  },
}));

import CitizenshipBadge, { formatCitizenshipClaimError } from '../CitizenshipBadge';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('formatCitizenshipClaimError (LEG-2922)', () => {
  it('preserves gameserver claim refusal detail', () => {
    const err = Object.assign(new Error('No colony ownership in this region'), {
      status: 400,
    });
    expect(formatCitizenshipClaimError(err)).toBe('No colony ownership in this region');
  });

  it('maps bare API Error: 403 to permission copy (LEG-4017 densify)', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatCitizenshipClaimError(err)).toBe(
      'You do not have permission to claim citizenship here.',
    );
  });
});

describe('CitizenshipBadge claim', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getMyMembership.mockReset();
    claimColonyCitizenship.mockReset();
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

  it('shows Claim when owns colony and not citizen, then POSTs', async () => {
    getMyMembership
      .mockResolvedValueOnce({
        region_id: 'r1',
        is_member: false,
        membership_type: null,
        stored_membership_type: null,
        owns_colony_in_region: true,
        can_vote: false,
        voting_power: 0,
        citizenship_source: null,
      })
      .mockResolvedValueOnce({
        region_id: 'r1',
        is_member: true,
        membership_type: 'citizen',
        stored_membership_type: 'citizen',
        owns_colony_in_region: true,
        can_vote: true,
        voting_power: 1,
        citizenship_source: 'colony',
      });
    claimColonyCitizenship.mockResolvedValue({ ok: true });

    await act(async () => {
      root.render(<CitizenshipBadge regionId="r1" regionName="Fringe" />);
    });
    await flush();

    const claim = container.querySelector(
      '[data-testid="citizenship-claim"]'
    ) as HTMLButtonElement;
    expect(claim).not.toBeNull();
    expect(container.textContent).toMatch(/VISITOR/i);

    await act(async () => {
      claim.click();
    });
    await flush();

    expect(claimColonyCitizenship).toHaveBeenCalledWith('r1');
    expect(getMyMembership).toHaveBeenCalledTimes(2);
    expect(container.textContent).toMatch(/CITIZEN/i);
    expect(container.querySelector('[data-testid="citizenship-claim"]')).toBeNull();
  });

  it('hides Claim when already a citizen', async () => {
    getMyMembership.mockResolvedValue({
      region_id: 'r1',
      is_member: true,
      membership_type: 'citizen',
      stored_membership_type: 'citizen',
      owns_colony_in_region: true,
      can_vote: true,
      voting_power: 1,
      citizenship_source: 'colony',
    });

    await act(async () => {
      root.render(<CitizenshipBadge regionId="r1" />);
    });
    await flush();

    expect(container.querySelector('[data-testid="citizenship-claim"]')).toBeNull();
    expect(claimColonyCitizenship).not.toHaveBeenCalled();
  });

  it('surfaces GS detail when claim API rejects', async () => {
    getMyMembership.mockResolvedValue({
      region_id: 'r1',
      is_member: false,
      membership_type: null,
      stored_membership_type: null,
      owns_colony_in_region: true,
      can_vote: false,
      voting_power: 0,
      citizenship_source: null,
    });
    claimColonyCitizenship.mockRejectedValue(
      Object.assign(new Error('Colony ownership required to claim citizenship'), {
        status: 400,
      }),
    );

    await act(async () => {
      root.render(<CitizenshipBadge regionId="r1" regionName="Fringe" />);
    });
    await flush();

    const claim = container.querySelector(
      '[data-testid="citizenship-claim"]'
    ) as HTMLButtonElement;
    expect(claim).not.toBeNull();

    await act(async () => {
      claim.click();
    });
    await flush();

    expect(container.querySelector('[data-testid="citizenship-claim-error"]')?.textContent).toBe(
      'Colony ownership required to claim citizenship',
    );
  });
});
