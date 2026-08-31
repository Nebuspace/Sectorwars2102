// @vitest-environment jsdom
/**
 * HaggleDesk — open → offer → accept/counter/reject money path
 * (WO-TESTCOV-PLAYER-HAGGLE-DESK-DEPTH). Complements taxInclusiveTotal suite
 * which only exercises the resumed-accepted terminal via status.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockStatus, mockOpen, mockOffer, mockQuote } = vi.hoisted(() => ({
  mockStatus: vi.fn(),
  mockOpen: vi.fn(),
  mockOffer: vi.fn(),
  mockQuote: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  haggleAPI: { status: mockStatus, open: mockOpen, offer: mockOffer },
  tradingAPI: { quote: mockQuote },
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

describe('HaggleDesk — negotiation money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onAccepted: (price: number) => void;
  let onBack: () => void;

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
          onBack={onBack}
          onAccepted={onAccepted}
        />,
      );
    });
    await act(async () => {
      await flush();
    });
  };

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    onAccepted = vi.fn<(price: number) => void>();
    onBack = vi.fn<() => void>();
    mockStatus.mockReset();
    mockOpen.mockReset();
    mockOffer.mockReset();
    mockQuote.mockReset();
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

  it('opens a session, accepts an offer, and Confirm fires onAccepted', async () => {
    mockOpen.mockResolvedValue(OPEN_CARD);
    mockOffer.mockResolvedValue({
      verdict: 'accept',
      round: 1,
      max_rounds: 4,
      commodity: 'ore',
      side: 'buy',
      status: 'accepted',
      fair_price: 25,
      agreed_price: 24,
    });
    mockQuote.mockResolvedValue({
      unit_price: 24,
      subtotal: 240,
      tax_rate: 0,
      tax: 0,
      total: 240,
    });

    await renderDesk();
    expect(mockStatus).toHaveBeenCalledWith('station-1', 'ore', 'buy');
    expect(container.querySelector('.haggle-open-btn')).toBeTruthy();

    await act(async () => {
      (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
      await flush();
    });

    expect(mockOpen).toHaveBeenCalledWith('station-1', 'ore', 'buy', 10);
    expect(container.textContent).toContain('Round 1 / 4');
    expect(container.querySelector('#haggle-offer-input')).toBeTruthy();

    await act(async () => {
      (container.querySelector('.haggle-submit-btn') as HTMLButtonElement).click();
      await flush();
    });

    expect(mockOffer).toHaveBeenCalledWith('station-1', 'ore', 'buy', 25);

    await act(async () => {
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain('Deal struck');
    });

    await vi.waitFor(() => {
      expect(mockQuote).toHaveBeenCalled();
    });

    const confirm = container.querySelector('.haggle-confirm-trade-btn') as HTMLButtonElement;
    expect(confirm).toBeTruthy();
    await act(async () => {
      confirm.click();
    });
    expect(onAccepted).toHaveBeenCalledWith(24);
  });

  it('surfaces a trader counter and pre-fills the counter price', async () => {
    mockOpen.mockResolvedValue(OPEN_CARD);
    mockOffer.mockResolvedValue({
      verdict: 'counter',
      round: 1,
      max_rounds: 4,
      commodity: 'ore',
      side: 'buy',
      status: 'open',
      fair_price: 25,
      counter_price: 27,
      next_round: 2,
      next_band: {
        fair_price: 26,
        accept_threshold: 29,
        reject_threshold: 19,
        side: 'buy',
      },
    });

    await renderDesk();
    await act(async () => {
      (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
      await flush();
    });
    await act(async () => {
      (container.querySelector('.haggle-submit-btn') as HTMLButtonElement).click();
      await flush();
    });

    expect(container.querySelector('.haggle-counter-line')?.textContent).toMatch(
      /counters at/,
    );
    expect((container.querySelector('#haggle-offer-input') as HTMLInputElement).value).toBe(
      '27',
    );
    expect(container.textContent).toContain('Round 2 / 4');
  });

  it('shows Walked away on reject and Back returns via onBack', async () => {
    mockOpen.mockResolvedValue(OPEN_CARD);
    mockOffer.mockResolvedValue({
      verdict: 'reject',
      round: 1,
      max_rounds: 4,
      commodity: 'ore',
      side: 'buy',
      status: 'rejected',
      fair_price: 25,
    });

    await renderDesk();
    await act(async () => {
      (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
      await flush();
    });
    await act(async () => {
      (container.querySelector('.haggle-submit-btn') as HTMLButtonElement).click();
      await flush();
    });

    expect(container.textContent).toContain('Walked away');
    await act(async () => {
      (container.querySelector('.haggle-back-btn') as HTMLButtonElement).click();
    });
    expect(onBack).toHaveBeenCalled();
  });

  it('blocks open when status reports locked', async () => {
    mockStatus.mockResolvedValue({
      commodity: 'ore',
      side: 'buy',
      locked: true,
      cooldown_remaining_seconds: 0,
      session: null,
    });
    await renderDesk();
    expect(container.textContent).toContain('Negotiations closed');
    expect(container.querySelector('.haggle-open-btn')).toBeNull();
  });

  describe('TypeError densify (LEG-3102)', () => {
    it('formatHaggleError falls back on TypeError network collapse', () => {
      const text = formatHaggleError(new TypeError('Failed to fetch'));
      expect(text).toBe('The trader turned away.');
      expect(text).not.toMatch(/Failed to fetch/i);
      expect(text).not.toMatch(/TypeError/i);
    });

    it('formatHaggleError preserves server detail for non-TypeError errors', () => {
      const err = Object.assign(new Error('request failed'), {
        response: { data: { detail: 'Commodity locked for this docking session.' } },
      });
      expect(formatHaggleError(err)).toBe('Commodity locked for this docking session.');
    });

    it('status TypeError surfaces honest fallback without Failed to fetch', async () => {
      mockStatus.mockRejectedValue(new TypeError('Failed to fetch'));
      await renderDesk();
      const alert = container.querySelector('.haggle-error');
      expect(alert?.textContent).toBe('The trader turned away.');
      expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
      expect(alert?.textContent).not.toMatch(/TypeError/i);
    });

    it('open TypeError surfaces honest fallback without Failed to fetch', async () => {
      mockOpen.mockRejectedValue(new TypeError('Failed to fetch'));
      await renderDesk();
      await act(async () => {
        (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
        await flush();
      });
      const alert = container.querySelector('.haggle-error');
      expect(alert?.textContent).toBe('The trader turned away.');
      expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
      expect(alert?.textContent).not.toMatch(/TypeError/i);
    });

    it('offer TypeError surfaces honest fallback without Failed to fetch', async () => {
      mockOpen.mockResolvedValue(OPEN_CARD);
      mockOffer.mockRejectedValue(new TypeError('Failed to fetch'));
      await renderDesk();
      await act(async () => {
        (container.querySelector('.haggle-open-btn') as HTMLButtonElement).click();
        await flush();
      });
      await act(async () => {
        (container.querySelector('.haggle-submit-btn') as HTMLButtonElement).click();
        await flush();
      });
      const alert = container.querySelector('.haggle-error');
      expect(alert?.textContent).toBe('The trader turned away.');
      expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
      expect(alert?.textContent).not.toMatch(/TypeError/i);
    });
  });
});
