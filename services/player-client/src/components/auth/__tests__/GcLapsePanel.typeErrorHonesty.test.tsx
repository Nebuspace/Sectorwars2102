// @vitest-environment jsdom
/**
 * LEG-3422 Soft-ORDER — GcLapsePanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatGcLapseRelocateError } from '../GcLapsePanel';

describe('GcLapsePanel TypeError densify (LEG-3422)', () => {
  it('formatGcLapseRelocateError falls back on TypeError network collapse', () => {
    const text = formatGcLapseRelocateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Relocation failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatGcLapseRelocateError(new Error('Network Error'))).toBe('Relocation failed');
    expect(formatGcLapseRelocateError(new Error('Failed to fetch'))).toBe('Relocation failed');
    expect(formatGcLapseRelocateError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatGcLapseRelocateError(new Error('Emergency relocation already used'))).toBe(
      'Emergency relocation already used',
    );
  });
});
