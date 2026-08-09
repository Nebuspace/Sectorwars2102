// @vitest-environment jsdom
/**
 * CitizenshipBadge — membership status + colony-claim affordance.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetMyMembership = vi.fn();
const mockClaimColonyCitizenship = vi.fn();

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    governanceAPI: {
      ...actual.governanceAPI,
      getMyMembership: (...args: unknown[]) => mockGetMyMembership(...args),
      claimColonyCitizenship: (...args: unknown[]) => mockClaimColonyCitizenship(...args),
    },
  };
});

import CitizenshipBadge from '../CitizenshipBadge';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('CitizenshipBadge', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetMyMembership.mockReset();
    mockClaimColonyCitizenship.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders nothing without a regionId', async () => {
    await act(async () => {
      root.render(<CitizenshipBadge />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('[data-testid="citizenship-badge"]')).toBeNull();
    expect(mockGetMyMembership).not.toHaveBeenCalled();
  });

  it('renders nothing when membership fetch fails (quiet degrade)', async () => {
    mockGetMyMembership.mockRejectedValue(new Error('no governance'));
    await act(async () => {
      root.render(<CitizenshipBadge regionId="reg-1" regionName="Fringe" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });
    expect(container.querySelector('[data-testid="citizenship-badge"]')).toBeNull();
  });

  it('shows CITIZEN · COLONY when on the roll via colony ownership', async () => {
    mockGetMyMembership.mockResolvedValue({
      region_id: 'reg-1',
      is_member: true,
      membership_type: 'citizen',
      stored_membership_type: null,
      owns_colony_in_region: true,
      can_vote: true,
      voting_power: 1,
      citizenship_source: 'colony',
    });
    await act(async () => {
      root.render(<CitizenshipBadge regionId="reg-1" regionName="Fringe" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });
    const badge = container.querySelector('[data-testid="citizenship-badge"]');
    expect(badge).toBeTruthy();
    expect(badge?.textContent).toContain('CITIZEN · COLONY');
    expect(container.querySelector('[data-testid="citizenship-claim"]')).toBeNull();
  });

  it('offers Claim when the player owns a colony but is not yet a stored citizen', async () => {
    mockGetMyMembership
      .mockResolvedValueOnce({
        region_id: 'reg-1',
        is_member: false,
        membership_type: null,
        stored_membership_type: null,
        owns_colony_in_region: true,
        can_vote: false,
        voting_power: 0,
        citizenship_source: null,
      })
      .mockResolvedValueOnce({
        region_id: 'reg-1',
        is_member: true,
        membership_type: 'citizen',
        stored_membership_type: 'citizen',
        owns_colony_in_region: true,
        can_vote: true,
        voting_power: 1,
        citizenship_source: 'colony',
      });
    mockClaimColonyCitizenship.mockResolvedValue({});

    await act(async () => {
      root.render(<CitizenshipBadge regionId="reg-1" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const claim = container.querySelector('[data-testid="citizenship-claim"]') as HTMLButtonElement;
    expect(claim).toBeTruthy();
    expect(container.textContent).toContain('VISITOR');

    await act(async () => {
      claim.click();
      await flush();
      await flush();
    });

    expect(mockClaimColonyCitizenship).toHaveBeenCalledWith('reg-1');
    await act(async () => {
      await flush();
      await flush();
    });
    expect(container.textContent).toContain('CITIZEN');
  });
});
