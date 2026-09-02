// @vitest-environment jsdom
/**
 * LEG-3488 Soft-ORDER — CombatInterface Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatCombatInitiateError } from '../CombatInterface';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('CombatInterface TypeError densify (LEG-3488)', () => {
  it('formatCombatInitiateError falls back on TypeError network collapse', () => {
    const text = formatCombatInitiateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Combat system error. Please try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatCombatInitiateError(new Error('Network Error'))).toBe(
      'Combat system error. Please try again.',
    );
    expect(formatCombatInitiateError(new Error('Failed to fetch'))).toBe(
      'Combat system error. Please try again.',
    );
    expect(formatCombatInitiateError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatCombatInitiateError(new Error('Target out of range'))).toBe('Target out of range');
  });

  it('surfaces 403/429 status paths and preserves server detail (LEG-3823)', () => {
    expect(formatCombatInitiateError(apiRequestError(403))).toBe(
      'You do not have permission to engage this target.',
    );
    expect(formatCombatInitiateError(apiRequestError(429))).toBe(
      'Combat action rate limit exceeded — wait a moment and try again.',
    );
    expect(formatCombatInitiateError(apiRequestError(403, 'engage_denied'))).toBe('engage_denied');
  });
});
