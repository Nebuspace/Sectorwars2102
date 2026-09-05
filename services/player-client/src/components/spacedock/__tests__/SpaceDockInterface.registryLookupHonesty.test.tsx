// @vitest-environment jsdom
/**
 * LEG-2959 Soft-ORDER — formatSpaceDockRegistryLookupError preserves GS detail
 * and keeps the named-pilot 404 copy.
 */
import { describe, it, expect } from 'vitest';
import { formatSpaceDockRegistryLookupError } from '../SpaceDockInterface';

describe('formatSpaceDockRegistryLookupError (LEG-2959)', () => {
  it('keeps named-pilot 404 copy', () => {
    const err = Object.assign(new Error('API Error: 404'), { status: 404 });
    expect(formatSpaceDockRegistryLookupError(err, 'Nova')).toBe(
      'No pilot named "Nova" on record.',
    );
  });

  it('preserves non-404 gameserver detail', () => {
    const err = Object.assign(new Error('Insufficient credits for registry lookup'), {
      status: 400,
    });
    expect(formatSpaceDockRegistryLookupError(err, 'Nova')).toBe(
      'Insufficient credits for registry lookup',
    );
  });

  it('uses 403 fallback when detail is a bare API Error blob', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatSpaceDockRegistryLookupError(err, 'Nova')).toBe(
      'Access denied — registry lookup is not available right now.',
    );
  });

  it('uses 429 rate-limit copy', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatSpaceDockRegistryLookupError(err)).toBe(
      'Registry lookup rate limit exceeded — wait a moment and try again.',
    );
  });

  it('falls back on TypeError network collapse (LEG-3575)', () => {
    const text = formatSpaceDockRegistryLookupError(new TypeError('Failed to fetch'), 'Nova');
    expect(text).toBe('Lookup failed.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3575)', () => {
    expect(formatSpaceDockRegistryLookupError(new Error('Network Error'), 'Nova')).toBe(
      'Lookup failed.',
    );
    expect(formatSpaceDockRegistryLookupError(new Error('Failed to fetch'), 'Nova')).toBe(
      'Lookup failed.',
    );
    expect(formatSpaceDockRegistryLookupError(new Error('Network Error'), 'Nova')).not.toMatch(
      /Network Error/i,
    );
  });
});
