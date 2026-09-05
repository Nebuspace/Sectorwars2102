// @vitest-environment jsdom
/**
 * LEG-3411 Soft-ORDER — LongTermMooringPanel Network Error densify.
 * LEG-4062 Soft-ORDER — HTTP 403/429 densify (invent=0).
 */
import { describe, it, expect } from 'vitest';
import { errMessage } from '../LongTermMooringPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('LongTermMooringPanel 403/429 densify (LEG-4062)', () => {
  it('errMessage maps 403/429 without raw transport leakage', () => {
    expect(errMessage(apiRequestError(403))).toBe(
      'Access denied — you cannot request long-term mooring right now.',
    );
    expect(errMessage(apiRequestError(403, 'mooring_denied'))).toBe('mooring_denied');
    expect(errMessage(apiRequestError(429))).toBe(
      'Long-term mooring rate limit exceeded — wait a moment and try again.',
    );
    expect(errMessage(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(errMessage(apiRequestError(403))).not.toMatch(/API Error/i);
    expect(errMessage(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
