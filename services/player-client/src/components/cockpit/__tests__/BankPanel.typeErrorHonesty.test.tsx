// @vitest-environment jsdom
/**
 * LEG-3413 Soft-ORDER — BankPanel Network Error densify.
 * LEG-4056 Soft-ORDER — HTTP 403/429 densify (invent=0).
 */
import { describe, it, expect } from 'vitest';
import { bankErrorMessage } from '../BankPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('BankPanel TypeError densify (LEG-3413)', () => {
  it('bankErrorMessage falls back on TypeError network collapse', () => {
    const text = bankErrorMessage(new TypeError('Failed to fetch'));
    expect(text).toBe('Bank request failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(bankErrorMessage(new Error('Network Error'))).toBe('Bank request failed');
    expect(bankErrorMessage(new Error('Failed to fetch'))).toBe('Bank request failed');
    expect(bankErrorMessage(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(bankErrorMessage(new Error('insufficient_balance'))).toBe('insufficient_balance');
  });
});

describe('BankPanel 403/429 densify (LEG-4056)', () => {
  it('bankErrorMessage maps 403/429 without raw transport leakage', () => {
    expect(bankErrorMessage(apiRequestError(403))).toBe(
      'Access denied — you cannot use the bank right now.',
    );
    expect(bankErrorMessage(apiRequestError(403, 'bank_denied'))).toBe('bank_denied');
    expect(bankErrorMessage(apiRequestError(429))).toBe(
      'Bank rate limit exceeded — wait a moment and try again.',
    );
    expect(bankErrorMessage(apiRequestError(429))).toMatch(/rate limit/i);
    expect(bankErrorMessage(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(bankErrorMessage(apiRequestError(403))).not.toMatch(/API Error/i);
    expect(bankErrorMessage(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
