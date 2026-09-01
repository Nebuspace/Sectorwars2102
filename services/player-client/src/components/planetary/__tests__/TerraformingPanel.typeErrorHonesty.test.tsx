// @vitest-environment jsdom
/**
 * LEG-3077 Soft-ORDER — TerraformingPanel TypeError densify.
 * Status/start/cancel/confirm must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import {
  formatTerraformingStatusError,
  formatTerraformingStartError,
  formatTerraformingCancelError,
  formatTerraformingConfirmBiomeError,
} from '../TerraformingPanel';

describe('TerraformingPanel TypeError densify (LEG-3077)', () => {
  it('formatTerraformingStatusError falls back on TypeError network collapse', () => {
    const text = formatTerraformingStatusError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load terraforming status');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTerraformingStartError falls back on TypeError network collapse', () => {
    const text = formatTerraformingStartError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to start terraforming');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTerraformingCancelError falls back on TypeError network collapse', () => {
    const text = formatTerraformingCancelError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to cancel terraforming');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTerraformingConfirmBiomeError falls back on TypeError network collapse', () => {
    const text = formatTerraformingConfirmBiomeError(new TypeError('Failed to fetch'));
    expect(text).toBe('Biome could not be confirmed yet.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTerraformingStatusError(new Error('status_denied'))).toBe('status_denied');
    expect(formatTerraformingStartError(new Error('start_denied'))).toBe('start_denied');
  });
});
