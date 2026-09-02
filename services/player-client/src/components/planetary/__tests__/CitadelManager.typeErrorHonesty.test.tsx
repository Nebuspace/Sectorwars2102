// @vitest-environment jsdom
/**
 * LEG-3468 Soft-ORDER — CitadelManager Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatCitadelLoadError,
  formatCitadelUpgradeError,
} from '../CitadelManager';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('CitadelManager TypeError densify (LEG-3468)', () => {
  it('formatCitadelLoadError falls back on TypeError network collapse', () => {
    const text = formatCitadelLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load citadel info');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatCitadelUpgradeError falls back on TypeError network collapse', () => {
    const text = formatCitadelUpgradeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Upgrade failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatCitadelLoadError(new Error('Network Error'))).toBe('Failed to load citadel info');
    expect(formatCitadelLoadError(new Error('Failed to fetch'))).toBe('Failed to load citadel info');
    expect(formatCitadelUpgradeError(new Error('Network Error'))).toBe('Upgrade failed');
    expect(formatCitadelUpgradeError(new Error('Failed to fetch'))).toBe('Upgrade failed');
    expect(formatCitadelLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatCitadelLoadError(new Error('citadel_offline'))).toBe('citadel_offline');
    expect(formatCitadelUpgradeError(new Error('upgrade_denied'))).toBe('upgrade_denied');
  });

  it('surfaces 403/429 status paths and preserves server detail (LEG-3826)', () => {
    expect(formatCitadelLoadError(apiRequestError(403))).toBe('You do not own this planet.');
    expect(formatCitadelLoadError(apiRequestError(429))).toBe(
      'Citadel lookup rate limit exceeded — wait a moment and try again.',
    );
    expect(formatCitadelLoadError(apiRequestError(403, 'planet_not_owned'))).toBe('planet_not_owned');
  });
});

describe('CitadelManager upgrade 403/429 densify (LEG-4024)', () => {
  it('formatCitadelUpgradeError surfaces 403/429 without raw status codes', () => {
    expect(formatCitadelUpgradeError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatCitadelUpgradeError(apiRequestError(403, 'upgrade_denied'))).toBe('upgrade_denied');
    expect(formatCitadelUpgradeError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatCitadelUpgradeError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatCitadelUpgradeError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
