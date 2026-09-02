// @vitest-environment jsdom
/**
 * LEG-3088 Soft-ORDER — TractorBeamInstallCta TypeError densify.
 * LEG-3562 Soft-ORDER — axios Network Error densify pin.
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

describe('TractorBeamInstallCta Network Error densify (LEG-3562)', () => {
  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatTractorBeamInstallError(new Error('Network Error'))).toBe(
      'Tractor Beam install failed',
    );
    expect(formatTractorBeamInstallError(new Error('Failed to fetch'))).toBe(
      'Tractor Beam install failed',
    );
    expect(formatTractorBeamInstallError(new Error('Network Error'))).not.toMatch(
      /Network Error/i,
    );
    expect(formatTractorBeamInstallError(new Error('Failed to fetch'))).not.toMatch(
      /Failed to fetch/i,
    );
  });
});

describe('formatTractorBeamInstallError 403/429 densify (LEG-4085)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatTractorBeamInstallError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTractorBeamInstallError(apiRequestError(403, 'tractor_denied'))).toBe(
      'tractor_denied',
    );
    expect(formatTractorBeamInstallError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTractorBeamInstallError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTractorBeamInstallError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
