// @vitest-environment jsdom
/**
 * LEG-3491 Soft-ORDER — GenesisDeployment Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatGenesisQuotesLoadError,
  formatGenesisVerifyError,
  formatGenesisDeployError,
} from '../GenesisDeployment';

describe('GenesisDeployment TypeError densify (LEG-3491)', () => {
  it('formatGenesisQuotesLoadError falls back on TypeError network collapse', () => {
    const text = formatGenesisQuotesLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load genesis pricing');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatGenesisVerifyError / formatGenesisDeployError fall back on Network Error', () => {
    expect(formatGenesisVerifyError(new Error('Network Error'))).toBe(
      'Failed to verify genesis pricing',
    );
    expect(formatGenesisDeployError(new Error('Failed to fetch'))).toBe(
      'Failed to deploy Genesis Device',
    );
    expect(formatGenesisQuotesLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatGenesisQuotesLoadError(new Error('insufficient_credits'))).toBe(
      'insufficient_credits',
    );
    expect(formatGenesisVerifyError(new Error('price_changed'))).toBe('price_changed');
    expect(formatGenesisDeployError(new Error('sector_occupied'))).toBe('sector_occupied');
  });
});
