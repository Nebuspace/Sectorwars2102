// @vitest-environment jsdom
/**
 * LEG-3090 Soft-ORDER — OwnershipTransferControl TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatOwnershipTransferError } from '../OwnershipTransferControl';

describe('OwnershipTransferControl TypeError densify (LEG-3090)', () => {
  it('formatOwnershipTransferError falls back on TypeError network collapse', () => {
    const text = formatOwnershipTransferError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Transfer request failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(
      formatOwnershipTransferError(new Error('Current owner cannot afford the 5% transfer fee.')),
    ).toBe('Current owner cannot afford the 5% transfer fee.');
  });
});
