// @vitest-environment jsdom
/**
 * LEG-3272 Soft-ORDER — GameDashboard TerraformHeaderPanel TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import { formatTerraformingStartError } from '../GameDashboard';

describe('formatTerraformingStartError TypeError densify (LEG-3272)', () => {
  it('maps TypeError Failed to fetch to Terraforming start failed', () => {
    const text = formatTerraformingStartError(new TypeError('Failed to fetch'));
    expect(text).toBe('Terraforming start failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('keeps server Error detail honesty', () => {
    expect(formatTerraformingStartError(new Error('Insufficient organics'))).toBe(
      'Insufficient organics',
    );
  });
});
