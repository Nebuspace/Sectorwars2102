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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('GenesisDeployment 403/429 densify (LEG-4004)', () => {
  it('formatGenesisQuotesLoadError maps 403/429 without raw transport strings', () => {
    expect(formatGenesisQuotesLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatGenesisQuotesLoadError(apiRequestError(403, 'quotes_denied'))).toBe(
      'quotes_denied',
    );
    expect(formatGenesisQuotesLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatGenesisQuotesLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatGenesisQuotesLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatGenesisQuotesLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatGenesisVerifyError maps 403/429 without raw transport strings', () => {
    expect(formatGenesisVerifyError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatGenesisVerifyError(apiRequestError(403, 'verify_denied'))).toBe('verify_denied');
    expect(formatGenesisVerifyError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatGenesisVerifyError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatGenesisVerifyError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatGenesisVerifyError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });

  it('formatGenesisDeployError maps 403/429 without raw transport strings', () => {
    expect(formatGenesisDeployError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatGenesisDeployError(apiRequestError(403, 'deploy_denied'))).toBe('deploy_denied');
    expect(formatGenesisDeployError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatGenesisDeployError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatGenesisDeployError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatGenesisDeployError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
