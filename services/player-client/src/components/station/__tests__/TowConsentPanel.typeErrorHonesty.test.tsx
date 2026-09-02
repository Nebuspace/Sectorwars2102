// @vitest-environment jsdom
/**
 * LEG-3089 Soft-ORDER — TowConsentPanel TypeError densify.
 * LEG-4063 Soft-ORDER — HTTP 403/429 densify (invent=0).
 */
import { describe, it, expect } from 'vitest';
import { formatTowActionError } from '../TowConsentPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('TowConsentPanel TypeError densify (LEG-3089)', () => {
  it('formatTowActionError falls back on TypeError network collapse', () => {
    const text = formatTowActionError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Tow action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTowActionError(new Error('tow_target_unavailable'))).toBe('tow_target_unavailable');
  });

  it('formatTowActionError falls back on axios Network Error / Failed to fetch (LEG-3364)', () => {
    expect(formatTowActionError(new Error('Network Error'))).toBe('Tow action failed');
    expect(formatTowActionError(new Error('Failed to fetch'))).toBe('Tow action failed');
    expect(formatTowActionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});

describe('TowConsentPanel 403/429 densify (LEG-4063)', () => {
  it('formatTowActionError maps 403/429 without raw transport leakage', () => {
    expect(formatTowActionError(apiRequestError(403))).toBe(
      'Access denied — you cannot perform this tow action right now.',
    );
    expect(formatTowActionError(apiRequestError(403, 'tow_denied'))).toBe('tow_denied');
    expect(formatTowActionError(apiRequestError(429))).toBe(
      'Tow action rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTowActionError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTowActionError(apiRequestError(403))).not.toMatch(/API Error/i);
    expect(formatTowActionError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
