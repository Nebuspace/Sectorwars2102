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

import OwnershipTransferControl from '../OwnershipTransferControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const OFFER = {
  from_player_id: 'owner-1',
  to_player_id: 'recipient-9',
  fee_credits: 999,
  fee_base: 19980,
  offered_at: '2026-08-19T22:00:00+00:00',
  expires_at: '2026-08-20T22:00:00+00:00',
};

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
});
