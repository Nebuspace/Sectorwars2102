// @vitest-environment jsdom
/**
 * LEG-3801 Soft-ORDER — SubscriptionResult typeErrorHonesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGet = vi.fn();

vi.mock('../../../services/apiClient', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

vi.mock('react-router-dom', () => ({
  useLocation: () => ({
    pathname: '/subscription/success',
    search: '?subscription_id=sub-123',
  }),
  useNavigate: () => vi.fn(),
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}));

import SubscriptionResult, {
  formatSubscriptionResultError,
  SUBSCRIPTION_RESULT_FETCH_FALLBACK,
} from '../SubscriptionResult';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatSubscriptionResultError (LEG-3801)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatSubscriptionResultError(
      new TypeError('Failed to fetch'),
      SUBSCRIPTION_RESULT_FETCH_FALLBACK,
    );
    expect(text).toBe(SUBSCRIPTION_RESULT_FETCH_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(
      formatSubscriptionResultError(new Error('Network Error'), SUBSCRIPTION_RESULT_FETCH_FALLBACK),
    ).toBe(SUBSCRIPTION_RESULT_FETCH_FALLBACK);
    expect(
      formatSubscriptionResultError(new Error('Failed to fetch'), SUBSCRIPTION_RESULT_FETCH_FALLBACK),
    ).toBe(SUBSCRIPTION_RESULT_FETCH_FALLBACK);
    expect(
      formatSubscriptionResultError(new Error('Network Error'), SUBSCRIPTION_RESULT_FETCH_FALLBACK),
    ).not.toMatch(/Network Error/i);
  });

  it('preserves axios detail for structured server errors', () => {
    const err = Object.assign(new Error('request failed'), {
      response: { data: { detail: 'Subscription not found for this account.' } },
    });
    expect(formatSubscriptionResultError(err, SUBSCRIPTION_RESULT_FETCH_FALLBACK)).toBe(
      'Subscription not found for this account.',
    );
  });

  it('preserves Error.message when no response detail', () => {
    expect(
      formatSubscriptionResultError(new Error('billing_provider_timeout'), SUBSCRIPTION_RESULT_FETCH_FALLBACK),
    ).toBe('billing_provider_timeout');
  });
});

describe('SubscriptionResult fetch transport collapse densify (LEG-3801)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGet.mockReset();
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

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
  ])('fetch %s rejection surfaces fallback without raw transport text', async (_label, err) => {
    mockGet.mockRejectedValue(err);

    await act(async () => {
      root.render(<SubscriptionResult />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const card = container.querySelector('.result-card.error');
    expect(card?.textContent).toContain(SUBSCRIPTION_RESULT_FETCH_FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
