// @vitest-environment jsdom
/**
 * LEG-3478 Soft-ORDER — BuildingManager Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatBuildingUpgradeError } from '../BuildingManager';

describe('BuildingManager TypeError densify (LEG-3478)', () => {
  it('formatBuildingUpgradeError falls back on TypeError network collapse', () => {
    const text = formatBuildingUpgradeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to upgrade building');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatBuildingUpgradeError(new Error('Network Error'))).toBe(
      'Failed to upgrade building',
    );
    expect(formatBuildingUpgradeError(new Error('Failed to fetch'))).toBe(
      'Failed to upgrade building',
    );
    expect(formatBuildingUpgradeError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatBuildingUpgradeError(new Error('upgrade_in_progress'))).toBe('upgrade_in_progress');
  });
});
