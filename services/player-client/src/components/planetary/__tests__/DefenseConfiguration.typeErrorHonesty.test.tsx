// @vitest-environment jsdom
/**
 * LEG-3481 Soft-ORDER — DefenseConfiguration Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatDefensePricingLoadError,
  formatDefenseUpdateError,
} from '../DefenseConfiguration';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('DefenseConfiguration TypeError densify (LEG-3481)', () => {
  it('formatDefenseUpdateError falls back on TypeError network collapse', () => {
    const text = formatDefenseUpdateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to update defenses');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatDefenseUpdateError(new Error('Network Error'))).toBe('Failed to update defenses');
    expect(formatDefenseUpdateError(new Error('Failed to fetch'))).toBe(
      'Failed to update defenses',
    );
    expect(formatDefenseUpdateError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatDefenseUpdateError(new Error('defenses_locked'))).toBe('defenses_locked');
  });
});

describe('DefenseConfiguration 403/429 densify (LEG-3976)', () => {
  it('formatDefenseUpdateError surfaces 403/429 without raw status codes', () => {
    expect(formatDefenseUpdateError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatDefenseUpdateError(apiRequestError(403, 'defenses_locked'))).toBe('defenses_locked');
    expect(formatDefenseUpdateError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatDefenseUpdateError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatDefenseUpdateError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });

  it('formatDefensePricingLoadError surfaces 403/429 without raw status codes', () => {
    expect(formatDefensePricingLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatDefensePricingLoadError(apiRequestError(403, 'pricing_denied'))).toBe(
      'pricing_denied',
    );
    expect(formatDefensePricingLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatDefensePricingLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatDefensePricingLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
