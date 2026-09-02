// @vitest-environment jsdom
/**
 * LEG-3800 Soft-ORDER — PayPalSubscription typeErrorHonesty.
 */
import { describe, expect, it } from 'vitest';

import {
  formatPayPalSubscriptionError,
  PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK,
  PAYPAL_SUBSCRIPTION_CREATE_FALLBACK,
} from '../PayPalSubscription';

describe('formatPayPalSubscriptionError (LEG-3800)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatPayPalSubscriptionError(
      new TypeError('Failed to fetch'),
      PAYPAL_SUBSCRIPTION_CREATE_FALLBACK,
    );
    expect(text).toBe(PAYPAL_SUBSCRIPTION_CREATE_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(
      formatPayPalSubscriptionError(new Error('Network Error'), PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK),
    ).toBe(PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK);
    expect(
      formatPayPalSubscriptionError(new Error('Failed to fetch'), PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK),
    ).toBe(PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK);
    expect(
      formatPayPalSubscriptionError(new Error('Network Error'), PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK),
    ).not.toMatch(/Network Error/i);
  });

  it('preserves axios detail for structured server errors', () => {
    const err = Object.assign(new Error('request failed'), {
      response: { data: { detail: 'PayPal approval URL unavailable.' } },
    });
    expect(formatPayPalSubscriptionError(err, PAYPAL_SUBSCRIPTION_CREATE_FALLBACK)).toBe(
      'PayPal approval URL unavailable.',
    );
  });

  it('uses cancel fallback for create/cancel catch paths on transport collapse', () => {
    expect(
      formatPayPalSubscriptionError(new TypeError('Failed to fetch'), PAYPAL_SUBSCRIPTION_CREATE_FALLBACK),
    ).toBe(PAYPAL_SUBSCRIPTION_CREATE_FALLBACK);
    expect(
      formatPayPalSubscriptionError(new Error('Network Error'), PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK),
    ).toBe(PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK);
    expect(
      formatPayPalSubscriptionError(new Error('Network Error'), PAYPAL_SUBSCRIPTION_CANCEL_FALLBACK),
    ).not.toMatch(/Network Error/i);
  });
});
