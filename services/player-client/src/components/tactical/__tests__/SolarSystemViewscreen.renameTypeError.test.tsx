// @vitest-environment jsdom
/**
 * LEG-3273 Soft-ORDER — SolarSystemViewscreen planet rename TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import { formatPlanetRenameError } from '../SolarSystemViewscreen';

describe('formatPlanetRenameError TypeError densify (LEG-3273)', () => {
  it('maps TypeError Failed to fetch to Rename failed', () => {
    const text = formatPlanetRenameError(new TypeError('Failed to fetch'));
    expect(text).toBe('Rename failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3305)', () => {
    expect(formatPlanetRenameError(new Error('Network Error'))).toBe('Rename failed');
    expect(formatPlanetRenameError(new Error('Failed to fetch'))).toBe('Rename failed');
    expect(formatPlanetRenameError(new Error('   '))).toBe('Rename failed');
  });

  it('keeps axios response detail honesty', () => {
    expect(
      formatPlanetRenameError({ response: { data: { detail: 'Name already taken' } } }),
    ).toBe('Name already taken');
  });
});
