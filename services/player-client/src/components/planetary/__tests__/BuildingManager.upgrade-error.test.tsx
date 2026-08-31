// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { formatBuildingUpgradeError } from '../BuildingManager';

describe('formatBuildingUpgradeError (LEG-2877)', () => {
  it('preserves gameserver 400 detail', () => {
    const err = Object.assign(new Error('An upgrade is already in progress'), {
      status: 400,
    });
    expect(formatBuildingUpgradeError(err)).toBe('An upgrade is already in progress');
  });

  it('falls back when message is bare API Error: 400', () => {
    const err = Object.assign(new Error('API Error: 400'), { status: 400 });
    expect(formatBuildingUpgradeError(err)).toBe('Failed to upgrade building');
  });

  it('falls back for non-Error values', () => {
    expect(formatBuildingUpgradeError(null)).toBe('Failed to upgrade building');
  });

  it('falls back on TypeError network collapse (LEG-3029)', () => {
    const text = formatBuildingUpgradeError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to upgrade building/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3277)', () => {
    expect(formatBuildingUpgradeError(new Error('Failed to fetch'))).toBe(
      'Failed to upgrade building',
    );
    expect(formatBuildingUpgradeError(new Error('Network Error'))).toBe(
      'Failed to upgrade building',
    );
    expect(formatBuildingUpgradeError(new Error('   '))).toBe('Failed to upgrade building');
  });
});
