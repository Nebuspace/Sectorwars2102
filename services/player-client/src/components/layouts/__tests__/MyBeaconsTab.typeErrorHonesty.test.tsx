// @vitest-environment jsdom
/**
 * LEG-3461 Soft-ORDER — MyBeaconsTab Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatBeaconDeployError,
  formatBeaconLoadError,
  formatBeaconRowActionError,
} from '../MyBeaconsTab';

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
