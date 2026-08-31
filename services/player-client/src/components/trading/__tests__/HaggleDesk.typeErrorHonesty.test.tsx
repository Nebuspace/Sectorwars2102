// @vitest-environment jsdom
/**
 * LEG-3241 Soft-ORDER — HaggleDesk DOM TypeError honesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockStatus, mockOpen, mockOffer } = vi.hoisted(() => ({
  mockStatus: vi.fn(),
  mockOpen: vi.fn(),
  mockOffer: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  haggleAPI: { status: mockStatus, open: mockOpen, offer: mockOffer },
  tradingAPI: { quote: vi.fn() },
}));

import HaggleDesk, { formatHaggleError } from '../HaggleDesk';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const OPEN_CARD = {
  status: 'open' as const,
  commodity: 'ore',
  side: 'buy' as const,
  quantity: 10,
  round: 1,
  max_rounds: 4,
  personality_type: 'shrewd',
  haggling_difficulty: 1,
  band: {
    fair_price: 25,
    accept_threshold: 28,
    reject_threshold: 18,
    side: 'buy' as const,
  },
  price_clamp: { min: 10, max: 50 },
};

describe('HaggleDesk TypeError honesty (LEG-3241)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockStatus.mockReset();
    mockOpen.mockReset();
    mockOffer.mockReset();
    mockStatus.mockResolvedValue({
      commodity: 'ore',
      side: 'buy',
      locked: false,
      cooldown_remaining_seconds: 0,
      session: null,
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  const renderDesk = async () => {
    await act(async () => {
      root.render(
        <HaggleDesk
          stationId="station-1"
          commodity="ore"
          side="buy"
          quantity={10}
          taxRate={0}
          commodityLabel="Ore"
          onBack={vi.fn()}
          onAccepted={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });
  };

  it('offer TypeError surfaces trader fallback without Failed to fetch / TypeError in DOM', async () => {
    mockOpen.mockResolvedValue(OPEN_CARD);
    mockOffer.mockRejectedValue(new TypeError('Failed to fetch'));

    await renderDesk();

    await act(async () => {
      (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
      await flush();
    });

    const submitBtn = container.querySelector('.haggle-submit-btn') as HTMLButtonElement;
    expect(submitBtn).toBeTruthy();

    await act(async () => {
      submitBtn.click();
      await flush();
    });

    const alert = container.querySelector('.haggle-error[role="alert"]');
    expect(alert?.textContent).toBe('The trader turned away.');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('open TypeError surfaces trader fallback without Failed to fetch / TypeError in DOM', async () => {
    mockOpen.mockRejectedValue(new TypeError('Failed to fetch'));

    await renderDesk();

    await act(async () => {
      (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
      await flush();
    });

    const alert = container.querySelector('.haggle-error[role="alert"]');
    expect(alert?.textContent).toBe('The trader turned away.');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('formatHaggleError falls back on axios Network Error (LEG-3505)', () => {
    const text = formatHaggleError(new Error('Network Error'));
    expect(text).toBe('The trader turned away.');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('open Network Error surfaces trader fallback without Network Error in DOM (LEG-3505)', async () => {
    mockOpen.mockRejectedValue(new Error('Network Error'));

    await renderDesk();

    await act(async () => {
      (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
      await flush();
    });

    const alert = container.querySelector('.haggle-error[role="alert"]');
    expect(alert?.textContent).toBe('The trader turned away.');
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
