// @vitest-environment jsdom
/**
 * LEG-3077 Soft-ORDER — TerraformingPanel TypeError densify.
 * LEG-3555 Soft-ORDER — Network Error densify.
 * LEG-4007 Soft-ORDER — 403/429 densify.
 * Status/start/cancel/confirm must not surface raw Failed to fetch / TypeError / Network Error.
 */
import { describe, it, expect } from 'vitest';
import {
  formatTerraformingStatusError,
  formatTerraformingStartError,
  formatTerraformingCancelError,
  formatTerraformingConfirmBiomeError,
} from '../TerraformingPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

describe('TerraformingPanel 403/429 densify (LEG-4007)', () => {
  it('status/start/cancel/confirm map 403/429 without raw transport strings', () => {
    expect(formatTerraformingStatusError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTerraformingStatusError(apiRequestError(403, 'status_denied'))).toBe(
      'status_denied',
    );
    expect(formatTerraformingStatusError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTerraformingStatusError(apiRequestError(429))).not.toMatch(/\b429\b/);

    expect(formatTerraformingStartError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTerraformingStartError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTerraformingCancelError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTerraformingCancelError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTerraformingConfirmBiomeError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTerraformingConfirmBiomeError(apiRequestError(429))).toMatch(/rate limit/i);

    expect(formatTerraformingStartError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatTerraformingCancelError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
