// @vitest-environment jsdom
/**
 * LEG-3411 Soft-ORDER — LongTermMooringPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { errMessage } from '../LongTermMooringPanel';

describe('LongTermMooringPanel TypeError densify (LEG-3411)', () => {
  it('errMessage falls back on TypeError network collapse', () => {
    const text = errMessage(new TypeError('Failed to fetch'));
    expect(text).toBe('Long-term mooring request failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(errMessage(new Error('Network Error'))).toBe('Long-term mooring request failed');
    expect(errMessage(new Error('Failed to fetch'))).toBe('Long-term mooring request failed');
    expect(errMessage(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(errMessage(new Error('mooring_slot_full'))).toBe('mooring_slot_full');
  });
});
