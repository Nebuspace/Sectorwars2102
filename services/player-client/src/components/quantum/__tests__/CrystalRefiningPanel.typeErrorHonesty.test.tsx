// @vitest-environment jsdom
/**
 * LEG-3161 Soft-ORDER — formatCrystalRefiningError TypeError densify.
 * LEG-3547 Soft-ORDER — Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatCrystalRefiningError } from '../CrystalRefiningPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatCrystalRefiningError (LEG-3161)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatCrystalRefiningError(new TypeError('Failed to fetch'), 'Crystal refine rejected.');
    expect(text).toBe('Crystal refine rejected.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch (LEG-3547)', () => {
    expect(formatCrystalRefiningError(new Error('Network Error'), 'Crystal refine rejected.')).toBe(
      'Crystal refine rejected.',
    );
    expect(formatCrystalRefiningError(new Error('Failed to fetch'), 'Crystal refine rejected.')).toBe(
      'Crystal refine rejected.',
    );
    expect(formatCrystalRefiningError(new Error('Network Error'), 'Crystal refine rejected.')).not.toMatch(
      /Network Error/i,
    );
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

describe('formatCrystalRefiningError 403/429 densify (LEG-4029)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    const fallback = 'Crystal refine rejected.';
    expect(formatCrystalRefiningError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatCrystalRefiningError(apiRequestError(403, 'refine_denied'), fallback)).toBe(
      'refine_denied',
    );
    expect(formatCrystalRefiningError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatCrystalRefiningError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatCrystalRefiningError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});
