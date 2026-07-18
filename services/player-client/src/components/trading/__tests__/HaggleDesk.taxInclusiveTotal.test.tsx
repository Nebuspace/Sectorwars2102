// @vitest-environment jsdom
/**
 * HaggleDesk — tax-inclusive "Deal struck" total (WO-API-B1, mack HIGH-2).
 *
 * Bug: HaggleDesk rendered agreed_price * quantity for the accepted-haggle
 * total, with ZERO tax, while buy_resource/sell_resource apply the
 * station's tax_rate on the haggled unit price exactly like any other
 * trade (via compute_buy_totals/compute_sell_totals). At any taxed
 * (owned) station this was deterministically shown != charged.
 *
 * Fix: once a session reaches the accepted terminal state, HaggleDesk
 * re-quotes POST /trading/quote (tradingAPI.quote) — which peeks the
 * accepted-but-unconsumed haggle price and applies tax via the SAME
 * compute_buy_totals/compute_sell_totals the commit path uses — and
 * renders THAT total instead of the local tax-less multiplication.
 *
 * This pins: (1) tradingAPI.quote is called with the right params once
 * accepted, (2) the rendered total is EXACTLY the quote's tax-inclusive
 * total (computed here via a local mirror of compute_buy_totals/
 * compute_sell_totals' int()-truncation arithmetic, not a hand-picked
 * number), and (3) the OLD tax-less agreed_price*quantity figure is gone
 * from the rendered total, not just augmented.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// player-client's vitest.config.ts has no setupFiles / IS_REACT_ACT_ENVIRONMENT,
// so a bare createRoot()+act() jsdom test logs baseline "not configured to
// support act(...)" console.error noise unrelated to this component (see
// .claude/agent-memory/monk/vitest-act-environment-noise.md) -- silence it
// the same way the other createRoot+act() suites in this repo do.
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

import HaggleDesk from '../HaggleDesk';

// Mirrors compute_buy_totals/compute_sell_totals (routes/trading.py)
// EXACTLY: int() truncation == Math.floor for the non-negative domain
// these always operate in.
const computeBuyTotal = (unitPrice: number, quantity: number, taxRate: number) => {
  const subtotal = unitPrice * quantity;
  const tax = Math.floor(subtotal * taxRate);
  return { subtotal, tax, total: subtotal + tax };
};
const computeSellTotal = (unitPrice: number, quantity: number, taxRate: number) => {
  const subtotal = unitPrice * quantity;
  const tax = Math.floor(subtotal * taxRate);
  return { subtotal, tax, total: subtotal - tax };
};

describe('HaggleDesk — tax-inclusive deal-struck total', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockStatus.mockReset();
    mockOpen.mockReset();
    mockOffer.mockReset();
    mockQuote.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  // Drives straight to the accepted terminal state via the "resumed
  // session" mount path (HaggleDesk.tsx's own status-check effect) rather
  // than open->offer->accept — a real, independently-exercised code path,
  // and the most direct way to reach `accepted` for this surface's test.
  const renderAcceptedSession = async (props: {
    side: 'buy' | 'sell';
    quantity: number;
    taxRate: number;
    agreedPrice: number;
  }) => {
    mockStatus.mockResolvedValue({
      commodity: 'ore',
      side: props.side,
      locked: false,
      cooldown_remaining_seconds: 0,
      session: { status: 'accepted', round: 2, max_rounds: 4, agreed_price: props.agreedPrice },
    });

    await act(async () => {
      root.render(
        <HaggleDesk
          stationId="station-1"
          commodity="ore"
          side={props.side}
          quantity={props.quantity}
          taxRate={props.taxRate}
          commodityLabel="Ore"
          onBack={vi.fn()}
          onAccepted={vi.fn()}
        />
      );
    });
    // Let the status-check effect's promise resolve, then the resulting
    // `accepted` state change's own tradingAPI.quote effect resolve too.
    await act(async () => {});
    await act(async () => {});
  };

  it('BUY at a taxed station: renders the tax-INCLUSIVE total from tradingAPI.quote, not agreed_price*quantity', async () => {
    const unitPrice = 27;
    const quantity = 10;
    const taxRate = 0.1;
    const { subtotal, tax, total } = computeBuyTotal(unitPrice, quantity, taxRate);
    // 27*10=270; floor(270*0.1)=27; total=297 -- sanity-check the fixture
    // itself exercises real truncation, not a coincidentally-round number.
    expect({ subtotal, tax, total }).toEqual({ subtotal: 270, tax: 27, total: 297 });

    mockQuote.mockResolvedValue({
      unit_price: unitPrice, subtotal, tax_rate: taxRate, tax, total,
    });

    await renderAcceptedSession({ side: 'buy', quantity, taxRate, agreedPrice: unitPrice });

    expect(mockQuote).toHaveBeenCalledWith('station-1', 'ore', quantity, 'buy');

    const totalText = container.querySelector('.haggle-agreed')?.textContent || '';
    expect(totalText).toContain('₡297');
    // The pre-fix tax-less figure (270) must NOT be what's shown as the total.
    expect(totalText).not.toContain('₡270');
    expect(totalText).toContain('10.0% station tax');
  });

  it('SELL at a taxed station: renders the tax-INCLUSIVE payout from tradingAPI.quote, not agreed_price*quantity', async () => {
    const unitPrice = 22;
    const quantity = 6;
    const taxRate = 0.15;
    const { subtotal, tax, total } = computeSellTotal(unitPrice, quantity, taxRate);
    // 22*6=132; floor(132*0.15)=19 (132*0.15=19.8, truncates not rounds); net=113.
    expect({ subtotal, tax, total }).toEqual({ subtotal: 132, tax: 19, total: 113 });

    mockQuote.mockResolvedValue({
      unit_price: unitPrice, subtotal, tax_rate: taxRate, tax, total,
    });

    await renderAcceptedSession({ side: 'sell', quantity, taxRate, agreedPrice: unitPrice });

    expect(mockQuote).toHaveBeenCalledWith('station-1', 'ore', quantity, 'sell');

    const totalText = container.querySelector('.haggle-agreed')?.textContent || '';
    expect(totalText).toContain('₡113');
    // The pre-fix tax-less figure (132, agreed_price*quantity with no
    // withholding) must NOT be what's shown as the total.
    expect(totalText).not.toContain('₡132');
    expect(totalText).toContain('15.0% station tax');
  });

  it('an untaxed station (taxRate=0) renders the same total the old formula did, with no tax note', async () => {
    const unitPrice = 30;
    const quantity = 5;
    mockQuote.mockResolvedValue({
      unit_price: unitPrice, subtotal: 150, tax_rate: 0, tax: 0, total: 150,
    });

    await renderAcceptedSession({ side: 'buy', quantity, taxRate: 0, agreedPrice: unitPrice });

    const totalText = container.querySelector('.haggle-agreed')?.textContent || '';
    expect(totalText).toContain('₡150');
    expect(totalText).not.toContain('station tax');
  });

  describe('accepted-quote fetch failure (mack MEDIUM rev-3)', () => {
    it('shows a PROMINENT warning + Retry (not the muted success-note style), and leaves Confirm enabled', async () => {
      const unitPrice = 27;
      const quantity = 10;
      mockQuote.mockRejectedValue(new Error('network down'));

      await renderAcceptedSession({ side: 'buy', quantity, taxRate: 0.1, agreedPrice: unitPrice });

      // Prominent banner present, with a Retry action.
      const banner = container.querySelector('.haggle-quote-error');
      expect(banner).not.toBeNull();
      expect(banner?.textContent).toContain("Couldn't confirm the tax-inclusive total");
      expect(banner?.textContent).toContain('correct amount');
      const retryBtn = container.querySelector('.haggle-quote-retry-btn') as HTMLButtonElement | null;
      expect(retryBtn).not.toBeNull();
      expect(retryBtn?.textContent).toContain('Retry');

      // The quiet, tax-CONFIRMED note only ever renders alongside a
      // resolved acceptedQuote (acceptedQuote.tax_rate > 0) -- on a
      // failure acceptedQuote is null, so it must not be present. The
      // fallback total shown is the tax-less estimate (labelled by the
      // prominent banner instead of a muted inline note).
      const totalText = container.querySelector('.haggle-agreed')?.textContent || '';
      expect(totalText).toContain(`₡${unitPrice * quantity}`); // 270 -- fallback, honestly unlabeled inline
      expect(totalText).not.toContain('incl.'); // no muted "(incl. X% station tax)" claim on a failed fetch

      // Confirm deliberately stays clickable through the error state --
      // the haggle session is single-use; blocking it on a persistently
      // flaky connection would strand an already-negotiated deal for zero
      // safety benefit (the real charge is server-authoritative regardless).
      const confirmBtn = container.querySelector('.haggle-confirm-trade-btn') as HTMLButtonElement | null;
      expect(confirmBtn?.disabled).toBe(false);
    });

    it('Retry re-calls tradingAPI.quote and clears the warning once it succeeds', async () => {
      const unitPrice = 27;
      const quantity = 10;
      const taxRate = 0.1;
      const { subtotal, tax, total } = computeBuyTotal(unitPrice, quantity, taxRate);
      mockQuote.mockRejectedValueOnce(new Error('network down'));

      await renderAcceptedSession({ side: 'buy', quantity, taxRate, agreedPrice: unitPrice });
      expect(container.querySelector('.haggle-quote-error')).not.toBeNull();
      expect(mockQuote).toHaveBeenCalledTimes(1);

      mockQuote.mockResolvedValueOnce({
        unit_price: unitPrice, subtotal, tax_rate: taxRate, tax, total,
      });
      const retryBtn = container.querySelector('.haggle-quote-retry-btn') as HTMLButtonElement;
      await act(async () => {
        retryBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      await act(async () => {});
      await act(async () => {});

      expect(mockQuote).toHaveBeenCalledTimes(2);
      expect(container.querySelector('.haggle-quote-error')).toBeNull();
      const totalText = container.querySelector('.haggle-agreed')?.textContent || '';
      expect(totalText).toContain(`₡${total}`);
      expect(totalText).toContain('10.0% station tax');
    });
  });
});
