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
const proposeStakeTransfer = vi.fn();
const approveStakeTransfer = vi.fn();
const rejectStakeTransfer = vi.fn();
const refreshPlayerState = vi.fn();

let mockPlayerState: { id?: string } | null = { id: 'owner-1' };

vi.mock('../../../services/api', () => ({
  portOwnershipAPI: {
    getSyndicateStatus: (...args: unknown[]) => getSyndicateStatus(...args),
    inviteShare: (...args: unknown[]) => inviteShare(...args),
    acceptShareInvite: (...args: unknown[]) => acceptShareInvite(...args),
    declineShareInvite: (...args: unknown[]) => declineShareInvite(...args),
    syndicateBuyout: (...args: unknown[]) => syndicateBuyout(...args),
    proposeStakeTransfer: (...args: unknown[]) => proposeStakeTransfer(...args),
    approveStakeTransfer: (...args: unknown[]) => approveStakeTransfer(...args),
    rejectStakeTransfer: (...args: unknown[]) => rejectStakeTransfer(...args),
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
    proposeStakeTransfer.mockReset();
    approveStakeTransfer.mockReset();
    rejectStakeTransfer.mockReset();
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

  it('shows stake-transfer propose form for syndicate shareholders (LEG-4237)', async () => {
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'syndicate',
      shares: [
        { player_id: 'owner-1', pct: 60 },
        { player_id: 'other-2', pct: 40 },
      ],
      pending_invites: [],
      is_primary: true,
    });

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    expect(container.querySelector('[data-testid="po-syndicate-xfer-form"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="po-syndicate-xfer-submit"]')).toBeTruthy();
  });

  it('propose applied path refreshes shares without inventing pending list (LEG-4237)', async () => {
    getSyndicateStatus
      .mockResolvedValueOnce({
        station_id: 'st-1',
        owner_id: 'owner-1',
        mode: 'syndicate',
        shares: [
          { player_id: 'owner-1', pct: 60 },
          { player_id: 'other-2', pct: 40 },
        ],
        pending_invites: [],
        is_primary: true,
      })
      .mockResolvedValueOnce({
        station_id: 'st-1',
        owner_id: 'owner-1',
        mode: 'syndicate',
        shares: [
          { player_id: 'owner-1', pct: 50 },
          { player_id: 'other-2', pct: 50 },
        ],
        pending_invites: [],
        is_primary: true,
      });
    proposeStakeTransfer.mockResolvedValue({
      proposal: {
        proposal_id: 'xfer-1',
        from_player_id: 'owner-1',
        to_player_id: 'other-2',
        pct: 10,
        status: 'applied',
        approvals: [{ player_id: 'owner-1' }],
      },
      shares: [
        { player_id: 'owner-1', pct: 50 },
        { player_id: 'other-2', pct: 50 },
      ],
    });

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const setReactInput = (el: HTMLInputElement, value: string) => {
      const proto = window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc?.set?.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const target = container.querySelector(
      '[data-testid="po-syndicate-xfer-target"]',
    ) as HTMLInputElement;
    const pct = container.querySelector(
      '[data-testid="po-syndicate-xfer-pct"]',
    ) as HTMLInputElement;
    await act(async () => {
      setReactInput(target, 'other-2');
      setReactInput(pct, '10');
      await flush();
    });

    const submit = container.querySelector(
      '[data-testid="po-syndicate-xfer-submit"]',
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
      await flush();
    });

    expect(proposeStakeTransfer).toHaveBeenCalledWith('st-1', 'other-2', 10);
    expect(refreshPlayerState).toHaveBeenCalled();
    expect(getSyndicateStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('[data-testid="po-syndicate-xfer-pending"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="po-syndicate-share-owner-1"]')?.textContent).toMatch(
      /50%/,
    );
  });

  it('pending propose hides Approve for auto-approved proposer; Reject remains (LEG-4237)', async () => {
    mockPlayerState = { id: 'other-2' };
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'syndicate',
      shares: [
        { player_id: 'owner-1', pct: 40 },
        { player_id: 'other-2', pct: 60 },
      ],
      pending_invites: [],
      is_primary: false,
    });
    proposeStakeTransfer.mockResolvedValue({
      proposal: {
        proposal_id: 'xfer-9',
        from_player_id: 'other-2',
        to_player_id: 'owner-1',
        pct: 5,
        status: 'pending',
        remaining_stake_pct: 95,
        approving_weight: 55,
        approvals: [{ player_id: 'other-2' }],
      },
    });

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const setReactInput = (el: HTMLInputElement, value: string) => {
      const proto = window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc?.set?.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    await act(async () => {
      setReactInput(
        container.querySelector('[data-testid="po-syndicate-xfer-target"]') as HTMLInputElement,
        'owner-1',
      );
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="po-syndicate-xfer-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    expect(container.querySelector('[data-testid="po-syndicate-xfer-pending"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="po-syndicate-xfer-approve-xfer-9"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="po-syndicate-xfer-reject-xfer-9"]')).toBeTruthy();
  });

  it('approve calls LEG-4236 route when pending card has Approve (LEG-4237)', async () => {
    // Owner holds 40% — propose 10% leaves remaining weight 30; threshold needs peer.
    // Approvals list empty in response so Approve stays visible for this seat (test harness).
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'syndicate',
      shares: [
        { player_id: 'owner-1', pct: 40 },
        { player_id: 'other-2', pct: 60 },
      ],
      pending_invites: [],
      is_primary: true,
    });
    proposeStakeTransfer.mockResolvedValue({
      proposal: {
        proposal_id: 'xfer-peer',
        from_player_id: 'owner-1',
        to_player_id: 'newbie-3',
        pct: 10,
        status: 'pending',
        remaining_stake_pct: 90,
        approving_weight: 0,
        approvals: [],
      },
    });
    approveStakeTransfer.mockResolvedValue({
      proposal: {
        proposal_id: 'xfer-peer',
        from_player_id: 'owner-1',
        to_player_id: 'newbie-3',
        pct: 10,
        status: 'applied',
        approvals: [{ player_id: 'owner-1' }],
      },
      shares: [
        { player_id: 'owner-1', pct: 30 },
        { player_id: 'other-2', pct: 60 },
        { player_id: 'newbie-3', pct: 10 },
      ],
    });

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const setReactInput = (el: HTMLInputElement, value: string) => {
      const proto = window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc?.set?.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    await act(async () => {
      setReactInput(
        container.querySelector('[data-testid="po-syndicate-xfer-target"]') as HTMLInputElement,
        'newbie-3',
      );
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="po-syndicate-xfer-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    const approve = container.querySelector(
      '[data-testid="po-syndicate-xfer-approve-xfer-peer"]',
    ) as HTMLButtonElement;
    expect(approve).toBeTruthy();
    await act(async () => {
      approve.click();
      await flush();
    });
    expect(approveStakeTransfer).toHaveBeenCalledWith('st-1', 'xfer-peer');
  });

  it('surfaces 403 stake-transfer propose with densify copy (LEG-4237)', async () => {
    getSyndicateStatus.mockResolvedValue({
      station_id: 'st-1',
      owner_id: 'owner-1',
      mode: 'syndicate',
      shares: [
        { player_id: 'owner-1', pct: 60 },
        { player_id: 'other-2', pct: 40 },
      ],
      pending_invites: [],
      is_primary: true,
    });
    proposeStakeTransfer.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<PortOfficeSyndicatePanel stationId="st-1" stationName="Dock" />);
      await flush();
    });

    const setReactInput = (el: HTMLInputElement, value: string) => {
      const proto = window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc?.set?.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    await act(async () => {
      setReactInput(
        container.querySelector('[data-testid="po-syndicate-xfer-target"]') as HTMLInputElement,
        'other-2',
      );
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="po-syndicate-xfer-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    const text = container.querySelector('[data-testid="po-syndicate-msg"]')?.textContent;
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });
});
