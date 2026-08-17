// @vitest-environment jsdom
/**
 * TradingVenue — hub chrome + TradingInterface host + black-market slot.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../trading/TradingInterface', () => ({
  default: () => <div data-testid="trading-interface" />,
}));

vi.mock('../LongTermMooringPanel', () => ({
  default: () => <div data-testid="long-term-mooring-panel" />,
}));

import TradingVenue from '../TradingVenue';

describe('TradingVenue', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
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

  it('renders Trading Hub chrome, hosts TradingInterface, and wires Back', async () => {
    const onBack = vi.fn();
    await act(async () => {
      root.render(
        <TradingVenue
          onBack={onBack}
          blackMarketButton={<button type="button">🖤 Black Market</button>}
        />,
      );
    });

    expect(container.textContent).toContain('Trading Hub');
    expect(container.querySelector('[data-testid="trading-interface"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="long-term-mooring-panel"]')).toBeTruthy();
    expect(container.textContent).toContain('Black Market');

    const back = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Back to Hub'),
    ) as HTMLButtonElement;
    await act(async () => {
      back.click();
    });
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
