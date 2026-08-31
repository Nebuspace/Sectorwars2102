// @vitest-environment jsdom
/**
 * LEG-3434 Soft-ORDER — InsuranceManager Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatInsuranceLoadError,
  formatInsurancePurchaseError,
} from '../InsuranceManager';

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
