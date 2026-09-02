// @vitest-environment jsdom
/**
 * LEG-3461 Soft-ORDER — MyBeaconsTab Network Error densify.
 * LEG-4019 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatBeaconDeployError,
  formatBeaconLoadError,
  formatBeaconRowActionError,
} from '../MyBeaconsTab';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('MyBeaconsTab TypeError densify (LEG-3461)', () => {
  it('formatBeaconLoadError falls back on TypeError network collapse', () => {
    const text = formatBeaconLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load your beacons');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatBeaconDeployError falls back on TypeError network collapse', () => {
    const text = formatBeaconDeployError(new TypeError('Failed to fetch'));
    expect(text).toBe('Deploy failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatBeaconRowActionError falls back on TypeError network collapse', () => {
    const text = formatBeaconRowActionError(new TypeError('Failed to fetch'));
    expect(text).toBe('Action failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatBeaconLoad/Deploy/RowAction fall back on axios Network Error / Failed to fetch', () => {
    expect(formatBeaconLoadError(new Error('Network Error'))).toBe('Failed to load your beacons');
    expect(formatBeaconLoadError(new Error('Failed to fetch'))).toBe('Failed to load your beacons');
    expect(formatBeaconDeployError(new Error('Network Error'))).toBe('Deploy failed');
    expect(formatBeaconDeployError(new Error('Failed to fetch'))).toBe('Deploy failed');
    expect(formatBeaconRowActionError(new Error('Network Error'))).toBe('Action failed');
    expect(formatBeaconRowActionError(new Error('Failed to fetch'))).toBe('Action failed');
    expect(formatBeaconLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatBeaconLoadError(new Error('beacon_store_offline'))).toBe('beacon_store_offline');
    expect(formatBeaconDeployError(new Error('deploy_denied'))).toBe('deploy_denied');
    expect(formatBeaconRowActionError(new Error('salvage_denied'))).toBe('salvage_denied');
  });
});

describe('MyBeaconsTab 403/429 densify (LEG-4019)', () => {
  it('formatBeaconDeploy/Load/RowAction map 403/429 without raw transport strings', () => {
    expect(formatBeaconDeployError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatBeaconDeployError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatBeaconLoadError(apiRequestError(403))).toMatch(/Access denied|permission/i);
    expect(formatBeaconLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatBeaconRowActionError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatBeaconRowActionError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatBeaconDeployError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatBeaconLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatBeaconLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
    expect(formatBeaconDeployError(apiRequestError(403, 'deploy_denied'))).toBe('deploy_denied');
  });
});

