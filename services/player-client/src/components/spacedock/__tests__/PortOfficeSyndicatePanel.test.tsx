// @vitest-environment jsdom
/**
 * PortOfficeSyndicatePanel — LEG-4117 co-ownership syndicate wire.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getSyndicateStatus = vi.fn();
const inviteShare = vi.fn();
const acceptShareInvite = vi.fn();
const declineShareInvite = vi.fn();
const syndicateBuyout = vi.fn();
const refreshPlayerState = vi.fn();

let mockPlayerState: { id?: string } | null = { id: 'owner-1' };

vi.mock('../../../services/api', () => ({
  portOwnershipAPI: {
    getSyndicateStatus: (...args: unknown[]) => getSyndicateStatus(...args),
    inviteShare: (...args: unknown[]) => inviteShare(...args),
    acceptShareInvite: (...args: unknown[]) => acceptShareInvite(...args),
    declineShareInvite: (...args: unknown[]) => declineShareInvite(...args),
    syndicateBuyout: (...args: unknown[]) => syndicateBuyout(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    refreshPlayerState,
  }),
}));

import PortOfficeSyndicatePanel, {
  formatSyndicateError,
} from '../PortOfficeSyndicatePanel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatSyndicateError (LEG-4117)', () => {
  const fallback = 'Syndicate action failed. Please try again.';

  it('densifies TypeError without transport strings', () => {
    expect(formatSyndicateError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatSyndicateError(new TypeError('Failed to fetch'), fallback)).not.toMatch(
      /TypeError/i,
    );
  });

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatSyndicateError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatSyndicateError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(formatSyndicateError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatSyndicateError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});

describe('PortOfficeSyndicatePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getSyndicateStatus.mockReset();
    inviteShare.mockReset();
    acceptShareInvite.mockReset();
    declineShareInvite.mockReset();
    syndicateBuyout.mockReset();
    refreshPlayerState.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    mockPlayerState = { id: 'owner-1' };
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'solo',
      shares: [{ player_id: 'owner-1', pct: 100 }],
      pending_invites: [],
      is_primary: true,
    });
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

  it('shows invite form for primary and issues share invite', async () => {
    inviteShare.mockResolvedValue({
      message: 'Share invite 15% issued at Dock',
      invite: { invite_id: 'inv-1', invitee_player_id: 'invitee-9', pct: 15 },
    });
    getSyndicateStatus
      .mockResolvedValueOnce({
        station_id: 'st-1',
        owner_id: 'owner-1',
        mode: 'solo',
        shares: [{ player_id: 'owner-1', pct: 100 }],
        pending_invites: [],
        is_primary: true,
      })
      .mockResolvedValueOnce({
        station_id: 'st-1',
        owner_id: 'owner-1',
        mode: 'solo',
        shares: [{ player_id: 'owner-1', pct: 100 }],
        pending_invites: [
          { invite_id: 'inv-1', invitee_player_id: 'invitee-9', pct: 15 },
        ],
        is_primary: true,
      });

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    expect(container.querySelector('[data-testid="po-syndicate-invite-form"]')).toBeTruthy();

    const invitee = container.querySelector(
      '[data-testid="po-syndicate-invitee"]',
    ) as HTMLInputElement;
    const pct = container.querySelector('[data-testid="po-syndicate-pct"]') as HTMLInputElement;

    const setReactInput = (el: HTMLInputElement, value: string) => {
      const proto = window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc?.set?.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };

    await act(async () => {
      setReactInput(invitee, 'invitee-9');
      setReactInput(pct, '15');
      await flush();
    });

    const submit = container.querySelector(
      '[data-testid="po-syndicate-invite-submit"]',
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    await act(async () => {
      submit.click();
      await flush();
    });

    expect(inviteShare).toHaveBeenCalledWith('st-1', 'invitee-9', 15);
    expect(refreshPlayerState).toHaveBeenCalled();
  });

  it('lets invitee accept a pending invite', async () => {
    mockPlayerState = { id: 'invitee-9' };
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'solo',
      shares: [{ player_id: 'owner-1', pct: 100 }],
      pending_invites: [
        { invite_id: 'inv-1', invitee_player_id: 'invitee-9', pct: 20 },
      ],
      is_primary: false,
    });
    acceptShareInvite.mockResolvedValue({
      message: 'Share invite accepted',
      conversion_fee: 1000,
    });

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="po-syndicate-accept-inv-1"]',
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
      await flush();
    });
    expect(acceptShareInvite).toHaveBeenCalledWith('st-1', 'inv-1');
  });

  it('surfaces 403 invite errors with player-safe copy', async () => {
    inviteShare.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const invitee = container.querySelector(
      '[data-testid="po-syndicate-invitee"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(invitee, 'invitee-9');
      invitee.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });

    const submit = container.querySelector(
      '[data-testid="po-syndicate-invite-submit"]',
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="po-syndicate-msg"]')?.textContent;
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('surfaces 429 buyout errors with player-safe copy', async () => {
    mockPlayerState = { id: 'owner-1' };
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'syndicate',
      shares: [
        { player_id: 'owner-1', pct: 70 },
        { player_id: 'other-2', pct: 30 },
      ],
      pending_invites: [],
      is_primary: true,
    });
    syndicateBuyout.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const arm = container.querySelector(
      '[data-testid="po-syndicate-buyout-arm"]',
    ) as HTMLButtonElement;
    await act(async () => {
      arm.click();
      await flush();
    });
    const confirm = container.querySelector(
      '[data-testid="po-syndicate-buyout-confirm-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      confirm.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="po-syndicate-msg"]')?.textContent;
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
  });
});
