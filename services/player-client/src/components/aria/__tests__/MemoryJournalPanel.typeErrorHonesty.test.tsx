// @vitest-environment jsdom
/**
 * LEG-3072 Soft-ORDER — formatAriaMemoryLoadError TypeError densify.
 * Memory load must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import { formatAriaMemoryLoadError } from '../MemoryJournalPanel';

describe('formatAriaMemoryLoadError (LEG-3072)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatAriaMemoryLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load memories');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves 503 server detail when present', () => {
    const err = Object.assign(new Error('ARIA memory recall temporarily unavailable'), {
      status: 503,
    });
    expect(formatAriaMemoryLoadError(err)).toBe(
      'ARIA memory recall temporarily unavailable',
    );
  });
});
