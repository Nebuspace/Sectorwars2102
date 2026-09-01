// @vitest-environment jsdom
/**
 * LEG-3672 Soft-ORDER — BountyPlaceCancel TypeError/Network Error densify.
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

import BountyPlaceCancel, {
  formatBountyCancelError,
  formatBountyInspectLoadError,
  formatBountyPlaceError,
} from '../BountyPlaceCancel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setInputValue(el: HTMLInputElement, value: string) {
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  nativeInputValueSetter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('BountyPlaceCancel TypeError densify (LEG-3672)', () => {
  it('formatBountyInspectLoadError falls back on TypeError network collapse', () => {
    const text = formatBountyInspectLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load bounties on target/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatBountyPlaceError falls back on axios Network Error / Failed to fetch', () => {
    expect(formatBountyPlaceError(new Error('Network Error'))).toBe('Failed to place bounty');
    expect(formatBountyPlaceError(new Error('Failed to fetch'))).toBe('Failed to place bounty');
    expect(formatBountyPlaceError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('formatBountyCancelError falls back on axios Network Error / Failed to fetch', () => {
    expect(formatBountyCancelError(new Error('Network Error'))).toBe('Failed to cancel bounty');
    expect(formatBountyCancelError(new Error('Failed to fetch'))).toBe('Failed to cancel bounty');
    expect(formatBountyCancelError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatBountyPlaceError(new Error('insufficient_credits'))).toBe('insufficient_credits');
    expect(formatBountyCancelError(new Error('not_placer'))).toBe('not_placer');
    expect(formatBountyInspectLoadError(new Error('target_hidden'))).toBe('target_hidden');
  });
});

describe('BountyPlaceCancel transport collapse densify (LEG-3672)', () => {
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

  it('inspect Network Error surfaces honest fallback without raw transport text', async () => {
    getOnTarget.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector('[data-testid="bounty-place-target"]') as HTMLInputElement;
    await act(async () => {
      setInputValue(target, 't1');
    });
    await act(async () => {
      (container.querySelector('[data-testid="bounty-inspect-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    const alert = container.querySelector('[data-testid="bounty-place-cancel-error"]');
    expect(alert?.textContent).toMatch(/Failed to load bounties on target/i);
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });

  it('place Network Error surfaces honest fallback without raw transport text', async () => {
    place.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector('[data-testid="bounty-place-target"]') as HTMLInputElement;
    await act(async () => {
      setInputValue(target, 't1');
    });
    await act(async () => {
      container.querySelector('[data-testid="bounty-place-form"]')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    const alert = container.querySelector('[data-testid="bounty-place-cancel-error"]');
    expect(alert?.textContent).toBe('Failed to place bounty');
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });

  it('cancel Network Error surfaces honest fallback without raw transport text', async () => {
    getOnTarget.mockResolvedValue({
      success: true,
      target_id: 't1',
      target_name: 'Rogue',
      total_value: 1000,
      player_bounties: [
        { id: 'b-mine', placed_by: 'placer-1', placed_by_name: 'Me', amount: 1000, type: 'player' },
      ],
      system_bounties: [],
    });
    cancel.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<BountyPlaceCancel />);
    });

    const target = container.querySelector('[data-testid="bounty-place-target"]') as HTMLInputElement;
    await act(async () => {
      setInputValue(target, 't1');
    });
    await act(async () => {
      (container.querySelector('[data-testid="bounty-inspect-submit"]') as HTMLButtonElement).click();
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="bounty-cancel-submit"]') as HTMLButtonElement).click();
      await flush();
    });

    const alert = container.querySelector('[data-testid="bounty-place-cancel-error"]');
    expect(alert?.textContent).toBe('Failed to cancel bounty');
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });
});
