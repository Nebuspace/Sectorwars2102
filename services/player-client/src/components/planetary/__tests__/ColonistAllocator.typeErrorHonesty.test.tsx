// @vitest-environment jsdom
/**
 * LEG-3479 Soft-ORDER — ColonistAllocator Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatColonistAllocateError } from '../ColonistAllocator';

describe('ColonistAllocator TypeError densify (LEG-3479)', () => {
  it('formatColonistAllocateError falls back on TypeError network collapse', () => {
    const text = formatColonistAllocateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to update allocations');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatColonistAllocateError(new Error('Network Error'))).toBe(
      'Failed to update allocations',
    );
    expect(formatColonistAllocateError(new Error('Failed to fetch'))).toBe(
      'Failed to update allocations',
    );
    expect(formatColonistAllocateError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatColonistAllocateError(new Error('overflow_denied'))).toBe('overflow_denied');
  });
});
