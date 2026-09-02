// @vitest-environment jsdom
/**
 * LEG-3434 Soft-ORDER — InsuranceManager Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatInsuranceLoadError,
  formatInsurancePurchaseError,
} from '../InsuranceManager';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('InsuranceManager TypeError densify (LEG-3434)', () => {
  it('formatInsuranceLoadError falls back on TypeError network collapse', () => {
    const text = formatInsuranceLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Insurance is unavailable right now.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatInsurancePurchaseError falls back on TypeError network collapse', () => {
    const text = formatInsurancePurchaseError(new TypeError('Failed to fetch'));
    expect(text).toBe('Purchase failed.');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatInsuranceLoadError(new Error('Network Error'))).toBe(
      'Insurance is unavailable right now.',
    );
    expect(formatInsurancePurchaseError(new Error('Network Error'))).toBe('Purchase failed.');
    expect(formatInsuranceLoadError(new Error('Failed to fetch'))).toBe(
      'Insurance is unavailable right now.',
    );
    expect(formatInsuranceLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatInsuranceLoadError(new Error('underwriter_offline'))).toBe('underwriter_offline');
    expect(formatInsurancePurchaseError(new Error('insufficient_credits'))).toBe(
      'insufficient_credits',
    );
  });
});

describe('InsuranceManager 403/429 densify (LEG-3986)', () => {
  it('formatInsuranceLoadError surfaces 403/429 without raw status codes', () => {
    expect(formatInsuranceLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatInsuranceLoadError(apiRequestError(403, 'insurance_view_denied'))).toBe(
      'insurance_view_denied',
    );
    expect(formatInsuranceLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatInsuranceLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatInsuranceLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });

  it('formatInsurancePurchaseError surfaces 403/429 without raw status codes', () => {
    expect(formatInsurancePurchaseError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatInsurancePurchaseError(apiRequestError(403, 'purchase_denied'))).toBe(
      'purchase_denied',
    );
    expect(formatInsurancePurchaseError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatInsurancePurchaseError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatInsurancePurchaseError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
