// @vitest-environment jsdom
/**
 * LEG-3081 Soft-ORDER — windshieldTableauPopupContent TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatBeaconPopupError } from '../windshieldTableauPopupContent';

describe('windshieldTableauPopupContent TypeError densify (LEG-3081)', () => {
  it('formatBeaconPopupError falls back on TypeError network collapse', () => {
    const text = formatBeaconPopupError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatBeaconPopupError(new Error('beacon_denied'))).toBe('beacon_denied');
  });

  it('formatBeaconPopupError falls back on axios Network Error / Failed to fetch (LEG-3363)', () => {
    expect(formatBeaconPopupError(new Error('Network Error'))).toBe('Action failed');
    expect(formatBeaconPopupError(new Error('Failed to fetch'))).toBe('Action failed');
    expect(formatBeaconPopupError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});
