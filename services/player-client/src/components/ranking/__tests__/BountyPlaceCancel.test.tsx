// @vitest-environment jsdom
/**
 * BountyPlaceCancel — LEG-2553 place + cancel callers for tip GS routes.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const place = vi.fn();
const cancel = vi.fn();
const getOnTarget = vi.fn();

vi.mock('../../../services/api', () => ({
  bountyAPI: {
    place: (...args: unknown[]) => place(...args),
    cancel: (...args: unknown[]) => cancel(...args),
    getOnTarget: (...args: unknown[]) => getOnTarget(...args),
  },
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'placer-1', username: 'Me' } }),
}));

import BountyPlaceCancel, { formatBountyInspectLoadError } from '../BountyPlaceCancel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setInputValue(el: HTMLInputElement, value: string) {
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  nativeInputValueSetter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('BountyPlaceCancel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    place.mockReset();
    cancel.mockReset();
    getOnTarget.mockReset();
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

  it('places a bounty then refreshes on-target offers', async () => {
    place.mockResolvedValue({
      success: true,
      bounty_id: 'b-new',
      total_cost: 1100,
      fee: 100,
    });
    getOnTarget.mockResolvedValue({
      success: true,
      target_id: 't1',
      target_name: 'Rogue',
      total_value: 1000,
      player_bounties: [
        { id: 'b-new', placed_by: 'placer-1', placed_by_name: 'Me', amount: 1000, type: 'player' },
      ],
      system_bounties: [],
    });

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector(
      '[data-testid="bounty-place-target"]',
    ) as HTMLInputElement;
    const amount = container.querySelector(
      '[data-testid="bounty-place-amount"]',
    ) as HTMLInputElement;

    await act(async () => {
      setInputValue(target, 't1');
      setInputValue(amount, '1000');
    });

    await act(async () => {
      container.querySelector('[data-testid="bounty-place-form"]')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(place).toHaveBeenCalledWith('t1', 1000);
    expect(getOnTarget).toHaveBeenCalledWith('t1');
    expect(container.querySelector('[data-testid="bounty-place-cancel-status"]')?.textContent)
      .toMatch(/Placed bounty b-new/i);
    expect(container.querySelector('[data-testid="bounty-mine-row"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bounty-cancel-submit"]')).toBeTruthy();
  });

  it('cancels only the placer own offer via tip cancel route', async () => {
    getOnTarget
      .mockResolvedValueOnce({
        success: true,
        target_id: 't1',
        target_name: 'Rogue',
        total_value: 6000,
        player_bounties: [
          { id: 'b-mine', placed_by: 'placer-1', placed_by_name: 'Me', amount: 2000, type: 'player' },
          { id: 'b-other', placed_by: 'other', placed_by_name: 'Other', amount: 4000, type: 'player' },
        ],
      })
      .mockResolvedValueOnce({
        success: true,
        target_id: 't1',
        target_name: 'Rogue',
        total_value: 4000,
        player_bounties: [
          { id: 'b-other', placed_by: 'other', placed_by_name: 'Other', amount: 4000, type: 'player' },
        ],
      });
    cancel.mockResolvedValue({ success: true, bounty_id: 'b-mine', refund: 2000 });

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector(
      '[data-testid="bounty-place-target"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(target, 't1');
    });
    await act(async () => {
      (container.querySelector('[data-testid="bounty-inspect-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    expect(getOnTarget).toHaveBeenCalledWith('t1');
    expect(container.querySelectorAll('[data-testid="bounty-mine-row"]').length).toBe(1);
    expect(container.querySelectorAll('[data-testid="bounty-other-row"]').length).toBe(1);
    expect(container.querySelector('[data-testid="bounty-other-row"]')?.textContent)
      .toMatch(/cannot cancel/i);

    await act(async () => {
      (container.querySelector('[data-testid="bounty-cancel-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    expect(cancel).toHaveBeenCalledWith('b-mine', 't1');
    expect(container.querySelector('[data-testid="bounty-place-cancel-status"]')?.textContent)
      .toMatch(/refunded 2,000/i);
  });

  it('surfaces place errors honestly', async () => {
    place.mockRejectedValue(new Error('Need 1100 credits (1000 + 100 fee), have 50'));

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector(
      '[data-testid="bounty-place-target"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(target, 't1');
    });
    await act(async () => {
      container.querySelector('[data-testid="bounty-place-form"]')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('[data-testid="bounty-place-cancel-error"]')?.textContent)
      .toMatch(/Need 1100 credits/i);
  });

  it('formatBountyInspectLoadError falls back on bare 404 without server detail', () => {
    const err = Object.assign(new Error('API Error: 404'), { status: 404 });
    expect(formatBountyInspectLoadError(err)).toBe('Target player not found.');
  });

  it('surfaces inspect 404 player-not-found server detail', async () => {
    getOnTarget.mockRejectedValueOnce(
      Object.assign(new Error('Player not found'), { status: 404 }),
    );

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector(
      '[data-testid="bounty-place-target"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(target, 'missing-player');
    });
    await act(async () => {
      (container.querySelector('[data-testid="bounty-inspect-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    expect(container.querySelector('[data-testid="bounty-place-cancel-error"]')?.textContent)
      .toBe('Player not found');
  });
});
