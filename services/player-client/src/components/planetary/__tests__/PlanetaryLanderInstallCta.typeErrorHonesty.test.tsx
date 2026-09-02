// @vitest-environment jsdom
/**
 * LEG-3493 Soft-ORDER — PlanetaryLanderInstallCta Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatPlanetaryLanderInstallError } from '../PlanetaryLanderInstallCta';


const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};
describe('PlanetaryLanderInstallCta TypeError densify (LEG-3493)', () => {
  it('formatPlanetaryLanderInstallError falls back on TypeError network collapse', () => {
    const text = formatPlanetaryLanderInstallError(new TypeError('Failed to fetch'));
    expect(text).toBe('Planetary Lander install failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatPlanetaryLanderInstallError(new Error('Network Error'))).toBe(
      'Planetary Lander install failed',
    );
    expect(formatPlanetaryLanderInstallError(new Error('Failed to fetch'))).toBe(
      'Planetary Lander install failed',
    );
    expect(formatPlanetaryLanderInstallError(new Error('Network Error'))).not.toMatch(
      /Network Error/i,
    );
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatPlanetaryLanderInstallError(new Error('insufficient_credits'))).toBe(
      'insufficient_credits',
    );
  });
});

describe('formatPlanetaryLanderInstallError 403/429 densify (LEG-4035)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatPlanetaryLanderInstallError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatPlanetaryLanderInstallError(apiRequestError(403, 'lander_denied'))).toBe('lander_denied');
    expect(formatPlanetaryLanderInstallError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatPlanetaryLanderInstallError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatPlanetaryLanderInstallError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
