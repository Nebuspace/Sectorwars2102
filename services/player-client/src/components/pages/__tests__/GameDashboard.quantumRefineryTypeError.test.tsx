// @vitest-environment jsdom
/**
 * LEG-3315 Soft-ORDER — GameDashboard QuantumRefineryStrip TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import { formatQuantumRefineryError } from '../GameDashboard';

describe('formatQuantumRefineryError TypeError densify (LEG-3315)', () => {
  it('maps TypeError Failed to fetch to Charge refinement failed', () => {
    const text = formatQuantumRefineryError(new TypeError('Failed to fetch'));
    expect(text).toBe('Charge refinement failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('keeps gameserver axios detail honesty', () => {
    const err = {
      response: { data: { detail: 'Must be docked at Class-3+ to refine' } },
    };
    expect(formatQuantumRefineryError(err)).toBe('Must be docked at Class-3+ to refine');
  });
});
