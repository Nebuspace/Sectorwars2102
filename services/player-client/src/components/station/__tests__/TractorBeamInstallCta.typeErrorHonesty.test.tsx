// @vitest-environment jsdom
/**
 * LEG-3088 Soft-ORDER — TractorBeamInstallCta TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTractorBeamInstallError } from '../TractorBeamInstallCta';

describe('TractorBeamInstallCta TypeError densify (LEG-3088)', () => {
  it('formatTractorBeamInstallError falls back on TypeError network collapse', () => {
    const text = formatTractorBeamInstallError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Tractor Beam install failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves axios detail when not TypeError', () => {
    const err = {
      response: { data: { detail: 'Insufficient credits for tractor beam install.' } },
    };
    expect(formatTractorBeamInstallError(err)).toBe('Insufficient credits for tractor beam install.');
  });
});
