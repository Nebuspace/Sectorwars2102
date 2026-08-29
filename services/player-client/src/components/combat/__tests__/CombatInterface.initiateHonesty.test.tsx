// @vitest-environment jsdom
/**
 * LEG-2932 Soft-ORDER — formatCombatInitiateError preserves GS detail.
 */
import { describe, it, expect } from 'vitest';
import { formatCombatInitiateError } from '../CombatInterface';

describe('formatCombatInitiateError', () => {
  it('preserves gameserver detail when present', () => {
    const err = Object.assign(new Error('Target out of range'), { status: 400 });
    expect(formatCombatInitiateError(err)).toBe('Target out of range');
  });

  it('falls back for bare API Error: N status text', () => {
    expect(formatCombatInitiateError(new Error('API Error: 500'))).toBe(
      'Combat system error. Please try again.',
    );
  });

  it('uses 403 fallback when detail absent', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatCombatInitiateError(err)).toBe(
      'You do not have permission to engage this target.',
    );
  });

  it('uses 429 rate-limit copy', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatCombatInitiateError(err)).toBe(
      'Combat action rate limit exceeded — wait a moment and try again.',
    );
  });
});
