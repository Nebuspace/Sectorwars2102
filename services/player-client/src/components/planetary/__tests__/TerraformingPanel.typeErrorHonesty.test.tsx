// @vitest-environment jsdom
/**
 * LEG-3077 Soft-ORDER — TerraformingPanel TypeError densify.
 * LEG-3555 Soft-ORDER — Network Error densify.
 * Status/start/cancel/confirm must not surface raw Failed to fetch / TypeError / Network Error.
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

  it('falls back on axios Network Error / Failed to fetch (LEG-3555)', () => {
    expect(formatTerraformingStatusError(new Error('Network Error'))).toBe(
      'Failed to load terraforming status',
    );
    expect(formatTerraformingStartError(new Error('Network Error'))).toBe(
      'Failed to start terraforming',
    );
    expect(formatTerraformingCancelError(new Error('Failed to fetch'))).toBe(
      'Failed to cancel terraforming',
    );
    expect(formatTerraformingConfirmBiomeError(new Error('Network Error'))).toBe(
      'Biome could not be confirmed yet.',
    );
    expect(formatTerraformingStatusError(new Error('Network Error'))).not.toMatch(/Network Error/i);
    expect(formatTerraformingStartError(new Error('Failed to fetch'))).not.toMatch(/Failed to fetch/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTerraformingStatusError(new Error('status_denied'))).toBe('status_denied');
    expect(formatTerraformingStartError(new Error('start_denied'))).toBe('start_denied');
  });
});
