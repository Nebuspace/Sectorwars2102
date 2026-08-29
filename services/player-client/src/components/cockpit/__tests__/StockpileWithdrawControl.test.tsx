// @vitest-environment jsdom
/**
 * StockpileWithdrawControl — teammate/visitor sibling for LEG-546.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import StockpileWithdrawControl from '../StockpileWithdrawControl';

const setInputValue = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
};

describe('StockpileWithdrawControl', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
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

  it('submits GS commodity key and amount', async () => {
    const onWithdraw = vi.fn();
    await act(async () => {
      root.render(<StockpileWithdrawControl onWithdraw={onWithdraw} />);
    });

    const amount = container.querySelector('input[aria-label="Stockpile amount"]') as HTMLInputElement;
    await act(async () => {
      setInputValue(amount, '25');
    });

    const submit = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('To cargo'),
    ) as HTMLButtonElement;
    await act(async () => {
      submit.click();
    });
    expect(onWithdraw).toHaveBeenCalledWith('fuel_ore', 25);
  });

  it('disables submit when amount is not a positive integer', async () => {
    const onWithdraw = vi.fn();
    await act(async () => {
      root.render(<StockpileWithdrawControl onWithdraw={onWithdraw} />);
    });
    const amount = container.querySelector('input[aria-label="Stockpile amount"]') as HTMLInputElement;
    await act(async () => {
      setInputValue(amount, '0');
    });
    const submit = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('To cargo'),
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });
});
