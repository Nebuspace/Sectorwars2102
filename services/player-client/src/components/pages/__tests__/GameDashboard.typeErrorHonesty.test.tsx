// @vitest-environment jsdom
/**
 * LEG-3736 Soft-ORDER — GameDashboard exported error formatters TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import {
  formatGameDashboardOpsError,
  formatQuantumRefineryError,
  formatTerraformingStartError,
} from '../GameDashboard';

describe('GameDashboard TypeError densify (LEG-3736)', () => {
  it('formatQuantumRefineryError falls back on TypeError network collapse', () => {
    const text = formatQuantumRefineryError(new TypeError('Failed to fetch'));
    expect(text).toBe('Charge refinement failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatQuantumRefineryError keeps gameserver axios detail', () => {
    const err = { response: { data: { detail: 'Must be docked at Class-3+ to refine' } } };
    expect(formatQuantumRefineryError(err)).toBe('Must be docked at Class-3+ to refine');
  });

  it('formatTerraformingStartError falls back on TypeError network collapse', () => {
    const text = formatTerraformingStartError(new TypeError('Failed to fetch'));
    expect(text).toBe('Terraforming start failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTerraformingStartError preserves server detail', () => {
    expect(formatTerraformingStartError(new Error('habitability_max_reached'))).toBe(
      'habitability_max_reached',
    );
  });

  it('formatGameDashboardOpsError densifies transport collapse and keeps server detail', () => {
    const fallback = 'Shield generator upgrade failed';
    const text = formatGameDashboardOpsError(new TypeError('Failed to fetch'), fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Failed to fetch/i);

    expect(formatGameDashboardOpsError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatGameDashboardOpsError(new Error('Network Error'), fallback)).not.toMatch(
      /Network Error/i,
    );

    const err = { response: { data: { detail: 'Defense grid prerequisite not met' } } };
    expect(formatGameDashboardOpsError(err, 'Citadel upgrade failed')).toBe(
      'Defense grid prerequisite not met',
    );
  });
});
