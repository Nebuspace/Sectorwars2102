// @vitest-environment jsdom
/**
 * LEG-2990 Soft-ORDER — InsuranceManager TypeError / network honesty.
 * Load + purchase must surface friendly fallbacks; never leak Failed to fetch / TypeError.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getInsurance = vi.fn();
const purchaseInsurance = vi.fn();

vi.mock('../../services/api', () => ({
  shipAPI: {
    getInsurance: (...a: unknown[]) => getInsurance(...a),
    purchaseInsurance: (...a: unknown[]) => purchaseInsurance(...a),
  },
}));

import InsuranceManager, {
  formatInsuranceLoadError,
  formatInsurancePurchaseError,
} from './InsuranceManager';

const flush = () => new Promise((r) => setTimeout(r, 0));

const STATUS = {
  ship_id: 'ship-1',
  ship_name: 'Hauler',
  ship_type: 'CARGO_HAULER',
  insurable: true,
  current_tier: 'NONE',
  purchase_value: 100000,
  current_payout_amount: 0,
  tiers: [
    {
      tier: 'BASIC',
      premium_pct: 0.1,
      premium_full: 10000,
      net_payout_pct: 0.5,
      payout_amount: 50000,
      upgrade_cost: 10000,
      purchasable: true,
    },
  ],
};

describe('formatInsurance*Error TypeError honesty (LEG-2990)', () => {
  it('formatInsuranceLoadError falls back on TypeError network collapse', () => {
    expect(formatInsuranceLoadError(new TypeError('Failed to fetch'))).toBe(
      'Insurance is unavailable right now.',
    );
  });

  it('formatInsurancePurchaseError falls back on TypeError network collapse', () => {
    expect(formatInsurancePurchaseError(new TypeError('Failed to fetch'))).toBe(
      'Purchase failed.',
    );
  });

  it('formatInsurancePurchaseError preserves gameserver detail', () => {
    expect(formatInsurancePurchaseError(new Error('Insufficient credits'))).toBe(
      'Insufficient credits',
    );
  });

  it('formatInsurancePurchaseError falls back on bare API Error status', () => {
    const err = Object.assign(new Error('API Error: 503'), { status: 503 });
    expect(formatInsurancePurchaseError(err)).toBe('Purchase failed.');
  });
});

describe('InsuranceManager TypeError honesty (LEG-2990)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getInsurance.mockReset();
    purchaseInsurance.mockReset();
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

  it('load TypeError surfaces unavailable copy without Failed to fetch / TypeError', async () => {
    getInsurance.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<InsuranceManager shipId="ship-1" playerCredits={0} />);
      await flush();
    });

    const alert = container.querySelector('[data-testid="ins-load-error"]');
    expect(alert).toBeTruthy();
    const text = alert!.textContent ?? '';
    expect(text).toMatch(/Insurance is unavailable right now/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('purchase TypeError surfaces Purchase failed without Failed to fetch / TypeError', async () => {
    getInsurance.mockResolvedValue(STATUS);
    purchaseInsurance.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<InsuranceManager shipId="ship-1" playerCredits={50000} />);
      await flush();
    });

    const buy = Array.from(container.querySelectorAll('button.ins-buy')).find((b) =>
      (b.textContent || '').includes('Insure'),
    );
    expect(buy).toBeTruthy();
    await act(async () => {
      buy!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
      await flush();
    });

    const err = container.querySelector('.ins-error[role="alert"]');
    expect(err).toBeTruthy();
    const text = err!.textContent ?? '';
    expect(text).toMatch(/Purchase failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
