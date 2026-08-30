// @vitest-environment jsdom
/**
 * OwnershipTransferControl — LEG-514 initiate / accept / error.
 * Fee is displayed from the mocked API field — never client-computed.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getOwnershipTransfer = vi.fn();
const offerOwnershipTransfer = vi.fn();
const acceptOwnershipTransfer = vi.fn();
const cancelOwnershipTransfer = vi.fn();

vi.mock('../../../services/api', () => ({
  planetaryAPI: {
    getOwnershipTransfer: (...args: unknown[]) => getOwnershipTransfer(...args),
    offerOwnershipTransfer: (...args: unknown[]) => offerOwnershipTransfer(...args),
    acceptOwnershipTransfer: (...args: unknown[]) => acceptOwnershipTransfer(...args),
    cancelOwnershipTransfer: (...args: unknown[]) => cancelOwnershipTransfer(...args),
  },
}));

import OwnershipTransferControl, {
  formatOwnershipTransferError,
} from '../OwnershipTransferControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const OFFER = {
  from_player_id: 'owner-1',
  to_player_id: 'recipient-9',
  fee_credits: 999,
  fee_base: 19980,
  offered_at: '2026-08-19T22:00:00+00:00',
  expires_at: '2026-08-20T22:00:00+00:00',
};

describe('formatOwnershipTransferError (LEG-2954)', () => {
  it('preserves gameserver transfer refusal detail', () => {
    const err = Object.assign(new Error('Current owner cannot afford the 5% transfer fee.'), {
      status: 400,
    });
    expect(formatOwnershipTransferError(err)).toBe(
      'Current owner cannot afford the 5% transfer fee.',
    );
  });

  it('falls back when message is bare API Error: 403', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatOwnershipTransferError(err)).toBe(
      'You do not have permission for this ownership transfer action.',
    );
  });

  it('uses load fallback for bare API Error without status context', () => {
    expect(formatOwnershipTransferError(new Error('API Error: 500'), 'Failed to load transfer status.')).toBe(
      'Failed to load transfer status.',
    );
  });

  it('falls back on TypeError network collapse (LEG-3048)', () => {
    const text = formatOwnershipTransferError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Transfer request failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('OwnershipTransferControl', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    getOwnershipTransfer.mockReset();
    offerOwnershipTransfer.mockReset();
    acceptOwnershipTransfer.mockReset();
    cancelOwnershipTransfer.mockReset();
    getOwnershipTransfer.mockResolvedValue({
      planet_id: 'planet-1',
      pending: false,
      offer: null,
    });
    offerOwnershipTransfer.mockResolvedValue({
      success: true,
      planet_id: 'planet-1',
      offer: OFFER,
    });
    acceptOwnershipTransfer.mockResolvedValue({
      success: true,
      planet_id: 'planet-1',
      from_player_id: 'owner-1',
      to_player_id: 'recipient-9',
      fee_credits: 999,
      owner_credits_remaining: 100,
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('owner initiate POSTs recipient_player_id via offerOwnershipTransfer', async () => {
    await act(async () => {
      root.render(
        <OwnershipTransferControl
          planetId="planet-1"
          isOwned
          currentPlayerId="owner-1"
        />,
      );
      await flush();
    });

    const input = container.querySelector(
      '[data-testid="ownership-transfer-recipient"]',
    ) as HTMLInputElement;
    expect(input).toBeTruthy();
    await act(async () => {
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      nativeInputValueSetter?.call(input, 'recipient-9');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });

    getOwnershipTransfer.mockResolvedValue({
      planet_id: 'planet-1',
      pending: true,
      offer: OFFER,
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="ownership-transfer-offer"]',
      ) as HTMLButtonElement).click();
      await flush();
    });

    expect(offerOwnershipTransfer).toHaveBeenCalledTimes(1);
    expect(offerOwnershipTransfer).toHaveBeenCalledWith('planet-1', 'recipient-9');
  });

  it('recipient accept POSTs /accept and shows API fee_credits (no client math)', async () => {
    getOwnershipTransfer.mockResolvedValue({
      planet_id: 'planet-1',
      pending: true,
      offer: OFFER,
    });

    await act(async () => {
      root.render(
        <OwnershipTransferControl
          planetId="planet-1"
          isOwned={false}
          currentPlayerId="recipient-9"
        />,
      );
      await flush();
    });

    const fee = container.querySelector('[data-testid="ownership-transfer-fee"]');
    expect(fee?.textContent).toContain('999');
    expect(fee?.textContent).not.toMatch(/5%/);

    await act(async () => {
      (container.querySelector(
        '[data-testid="ownership-transfer-accept"]',
      ) as HTMLButtonElement).click();
      await flush();
    });

    expect(acceptOwnershipTransfer).toHaveBeenCalledTimes(1);
    expect(acceptOwnershipTransfer).toHaveBeenCalledWith('planet-1');
  });

  it('surfaces GS detail on insufficient-credits error', async () => {
    offerOwnershipTransfer.mockRejectedValue(
      new Error('Current owner cannot afford the 5% transfer fee.'),
    );

    await act(async () => {
      root.render(
        <OwnershipTransferControl
          planetId="planet-1"
          isOwned
          currentPlayerId="owner-1"
        />,
      );
      await flush();
    });

    const input = container.querySelector(
      '[data-testid="ownership-transfer-recipient"]',
    ) as HTMLInputElement;
    await act(async () => {
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      nativeInputValueSetter?.call(input, 'recipient-9');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="ownership-transfer-offer"]',
      ) as HTMLButtonElement).click();
      await flush();
    });

    expect(container.querySelector('[data-testid="ownership-transfer-error"]')?.textContent)
      .toBe('Current owner cannot afford the 5% transfer fee.');
  });

  it('surfaces load 403 permission error in the alert (not silent empty panel)', async () => {
    getOwnershipTransfer.mockRejectedValue(
      apiRequestError(403, 'Only the planet owner may view transfer status.'),
    );

    await act(async () => {
      root.render(
        <OwnershipTransferControl
          planetId="planet-1"
          isOwned
          currentPlayerId="owner-1"
        />,
      );
      await flush();
    });

    const alert = container.querySelector('[data-testid="ownership-transfer-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Only the planet owner may view transfer status.');
    expect(container.querySelector('[data-testid="ownership-transfer-control"]')).toBeTruthy();
  });

  it('surfaces transfer offer 429 rate-limit error in the alert', async () => {
    offerOwnershipTransfer.mockRejectedValue(
      apiRequestError(429, 'Rate limit exceeded. Try again later.'),
    );

    await act(async () => {
      root.render(
        <OwnershipTransferControl
          planetId="planet-1"
          isOwned
          currentPlayerId="owner-1"
        />,
      );
      await flush();
    });

    const input = container.querySelector(
      '[data-testid="ownership-transfer-recipient"]',
    ) as HTMLInputElement;
    await act(async () => {
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      nativeInputValueSetter?.call(input, 'recipient-9');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });

    await act(async () => {
      (container.querySelector(
        '[data-testid="ownership-transfer-offer"]',
      ) as HTMLButtonElement).click();
      await flush();
    });

    const alert = container.querySelector('[data-testid="ownership-transfer-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toMatch(/rate limit exceeded/i);
  });
});
