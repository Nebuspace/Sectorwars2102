// @vitest-environment jsdom
/**
 * LEG-3480 Soft-ORDER — ColonySpecialization Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatColonySpecializeError } from '../ColonySpecialization';

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
