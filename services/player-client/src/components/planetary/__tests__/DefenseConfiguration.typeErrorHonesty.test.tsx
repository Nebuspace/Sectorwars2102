// @vitest-environment jsdom
/**
 * LEG-3481 Soft-ORDER — DefenseConfiguration Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatDefenseUpdateError } from '../DefenseConfiguration';

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
