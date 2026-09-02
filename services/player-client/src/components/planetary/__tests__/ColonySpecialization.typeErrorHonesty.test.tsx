// @vitest-environment jsdom
/**
 * LEG-3480 Soft-ORDER — ColonySpecialization Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatColonySpecializeError } from '../ColonySpecialization';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('ColonySpecialization TypeError densify (LEG-3480)', () => {
  it('formatColonySpecializeError falls back on TypeError network collapse', () => {
    const text = formatColonySpecializeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to specialize colony');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatColonySpecializeError(new Error('Network Error'))).toBe(
      'Failed to specialize colony',
    );
    expect(formatColonySpecializeError(new Error('Failed to fetch'))).toBe(
      'Failed to specialize colony',
    );
    expect(formatColonySpecializeError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatColonySpecializeError(new Error('spec_locked'))).toBe('spec_locked');
  });
});

describe('ColonySpecialization 403/429 densify (LEG-4026)', () => {
  it('formatColonySpecializeError surfaces 403/429 without raw status codes', () => {
    expect(formatColonySpecializeError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatColonySpecializeError(apiRequestError(403, 'spec_denied'))).toBe('spec_denied');
    expect(formatColonySpecializeError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatColonySpecializeError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatColonySpecializeError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
