// @vitest-environment jsdom
/**
 * LEG-3414 Soft-ORDER — FleetManagerPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatFleetManagerError } from '../FleetManagerPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('FleetManagerPanel TypeError densify (LEG-3414)', () => {
  it('formatFleetManagerError falls back on TypeError network collapse', () => {
    const text = formatFleetManagerError(new TypeError('Failed to fetch'));
    expect(text).toBe('Fleet request failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatFleetManagerError(new Error('Network Error'))).toBe('Fleet request failed');
    expect(formatFleetManagerError(new Error('Failed to fetch'))).toBe('Fleet request failed');
    expect(formatFleetManagerError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatFleetManagerError(new Error('fleet_busy'))).toBe('fleet_busy');
  });
});

describe('FleetManagerPanel 403/429 densify (LEG-4003)', () => {
  it('formatFleetManagerError surfaces 403/429 without raw status codes', () => {
    expect(formatFleetManagerError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatFleetManagerError(apiRequestError(403, 'fleet_busy_denied'))).toBe(
      'fleet_busy_denied',
    );
    expect(formatFleetManagerError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatFleetManagerError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatFleetManagerError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatFleetManagerError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
