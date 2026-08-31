// @vitest-environment jsdom
/**
 * LEG-3413 Soft-ORDER — BankPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { bankErrorMessage } from '../BankPanel';

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
