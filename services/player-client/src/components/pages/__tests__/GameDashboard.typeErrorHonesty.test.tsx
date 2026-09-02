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

describe('GameDashboard 403/429 densify (LEG-4099)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };

  it('formatQuantumRefineryError surfaces 403/429 without raw status codes', () => {
    expect(formatQuantumRefineryError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatQuantumRefineryError(apiRequestError(403, 'refine_denied'))).toBe('refine_denied');
    expect(formatQuantumRefineryError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatQuantumRefineryError(apiRequestError(429))).not.toMatch(/\b429\b/);
  });

  it('formatTerraformingStartError surfaces 403/429 without raw status codes', () => {
    expect(formatTerraformingStartError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTerraformingStartError(apiRequestError(403, 'terraform_denied'))).toBe(
      'terraform_denied',
    );
    expect(formatTerraformingStartError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTerraformingStartError(apiRequestError(429))).not.toMatch(/\b429\b/);
  });

  it('formatGameDashboardOpsError surfaces 403/429 without raw status codes', () => {
    const fallback = 'Shield generator upgrade failed';
    expect(formatGameDashboardOpsError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatGameDashboardOpsError(apiRequestError(403, 'ops_denied'), fallback)).toBe(
      'ops_denied',
    );
    expect(formatGameDashboardOpsError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatGameDashboardOpsError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});
