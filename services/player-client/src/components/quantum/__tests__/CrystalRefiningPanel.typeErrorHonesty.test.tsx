// @vitest-environment jsdom
/**
 * LEG-3161 Soft-ORDER — formatCrystalRefiningError TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatCrystalRefiningError } from '../CrystalRefiningPanel';

describe('formatCrystalRefiningError (LEG-3161)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatCrystalRefiningError(new TypeError('Failed to fetch'), 'Crystal refine rejected.');
    expect(text).toBe('Crystal refine rejected.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves axios detail for non-TypeError errors', () => {
    const err = Object.assign(new Error('insufficient shards'), {
      response: { data: { detail: 'Need 5 shards to refine.' } },
    });
    expect(formatCrystalRefiningError(err, 'Crystal refine rejected.')).toBe('Need 5 shards to refine.');
  });

  it('preserves Error.message when no response detail', () => {
    expect(formatCrystalRefiningError(new Error('Dock required'), 'Crystal refine rejected.')).toBe(
      'Dock required',
    );
  });
});
