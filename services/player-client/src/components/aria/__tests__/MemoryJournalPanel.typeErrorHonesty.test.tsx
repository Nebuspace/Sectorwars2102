// @vitest-environment jsdom
/**
 * LEG-3072 Soft-ORDER — formatAriaMemoryLoadError TypeError densify.
 * Memory load must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import { formatAriaMemoryLoadError, formatAriaMemoryActionError } from '../MemoryJournalPanel';


const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};
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

describe('formatAriaMemoryActionError network-collapse densify (LEG-3306)', () => {
  it('falls back on axios Network Error / Failed to fetch / empty', () => {
    expect(formatAriaMemoryActionError(new Error('Network Error'), 'ARIA memory export failed.')).toBe(
      'ARIA memory export failed.',
    );
    expect(formatAriaMemoryActionError(new Error('Failed to fetch'), 'ARIA memory reset failed.')).toBe(
      'ARIA memory reset failed.',
    );
    expect(formatAriaMemoryActionError(new Error('   '), 'ARIA memory export failed.')).toBe(
      'ARIA memory export failed.',
    );
    expect(formatAriaMemoryActionError(new Error('export quota exceeded'), 'ARIA memory export failed.')).toBe(
      'export quota exceeded',
    );
  });
});

describe('formatAriaMemoryLoadError / formatAriaMemoryActionError 403/429 densify (LEG-4031)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatAriaMemoryLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatAriaMemoryLoadError(apiRequestError(403, 'memory_denied'))).toBe('memory_denied');
    expect(formatAriaMemoryLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatAriaMemoryLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatAriaMemoryActionError(apiRequestError(403), 'ARIA memory export failed.')).toMatch(
      /permission/i,
    );
    expect(formatAriaMemoryActionError(apiRequestError(403, 'reset_denied'), 'ARIA memory reset failed.')).toBe(
      'reset_denied',
    );
    expect(formatAriaMemoryActionError(apiRequestError(429), 'ARIA memory export failed.')).toMatch(
      /rate limit/i,
    );
    expect(formatAriaMemoryActionError(apiRequestError(403), 'ARIA memory export failed.')).not.toMatch(
      /TypeError/i,
    );
  });
});
