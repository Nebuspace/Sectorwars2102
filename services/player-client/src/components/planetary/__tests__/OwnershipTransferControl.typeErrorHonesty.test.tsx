// @vitest-environment jsdom
/**
 * LEG-3090 Soft-ORDER — OwnershipTransferControl TypeError densify.
 * LEG-3546 Soft-ORDER — Network Error densify.
 * LEG-4012 Soft-ORDER — 403/429 densify.
 */
import { describe, it, expect } from 'vitest';
import { formatOwnershipTransferError } from '../OwnershipTransferControl';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('OwnershipTransferControl TypeError densify (LEG-3090)', () => {
  it('formatOwnershipTransferError falls back on TypeError network collapse', () => {
    const text = formatOwnershipTransferError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Transfer request failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch (LEG-3546)', () => {
    expect(formatOwnershipTransferError(new Error('Network Error'))).toBe('Transfer request failed.');
    expect(formatOwnershipTransferError(new Error('Failed to fetch'))).toBe('Transfer request failed.');
    expect(formatOwnershipTransferError(new Error('Network Error'))).not.toMatch(/Network Error/i);
    expect(formatOwnershipTransferError(new Error('Failed to fetch'))).not.toMatch(/Failed to fetch/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(
      formatOwnershipTransferError(new Error('Current owner cannot afford the 5% transfer fee.')),
    ).toBe('Current owner cannot afford the 5% transfer fee.');
  });
});

describe('OwnershipTransferControl 403/429 densify (LEG-4012)', () => {
  it('formatOwnershipTransferError maps 403/429 without raw transport strings', () => {
    expect(formatOwnershipTransferError(apiRequestError(403))).toBe(
      'You do not have permission for this ownership transfer action.',
    );
    expect(
      formatOwnershipTransferError(apiRequestError(403, 'Current owner cannot afford the 5% transfer fee.')),
    ).toBe('Current owner cannot afford the 5% transfer fee.');
    expect(formatOwnershipTransferError(apiRequestError(429))).toBe(
      'Ownership-transfer rate limit exceeded — wait a moment and try again.',
    );
    expect(formatOwnershipTransferError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatOwnershipTransferError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatOwnershipTransferError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});
