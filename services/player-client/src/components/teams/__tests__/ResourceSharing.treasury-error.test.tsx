// @vitest-environment jsdom
/**
 * ResourceSharing — treasury op honest error copy (LEG-2918).
 * GS: deposit 400 detail=str(e); withdraw/transfer 403 detail=str(e).
 */
import { describe, expect, it } from 'vitest';
import { formatTreasuryOpError } from '../ResourceSharing';

describe('formatTreasuryOpError (LEG-2918)', () => {
  it('preserves gameserver deposit 400 detail', () => {
    const err = Object.assign(new Error('Insufficient personal credits for deposit'), {
      status: 400,
    });
    expect(formatTreasuryOpError(err)).toBe('Insufficient personal credits for deposit');
  });

  it('preserves gameserver withdraw 403 detail', () => {
    const err = Object.assign(new Error('Treasury permission required to withdraw'), {
      status: 403,
    });
    expect(formatTreasuryOpError(err)).toBe('Treasury permission required to withdraw');
  });

  it('preserves transfer 403 detail from axios-shaped response.data.detail', () => {
    const err = {
      response: {
        status: 403,
        data: { detail: 'Only treasury managers may transfer' },
      },
    };
    expect(formatTreasuryOpError(err)).toBe('Only treasury managers may transfer');
  });

  it('falls back when message is bare API Error: 400', () => {
    const err = Object.assign(new Error('API Error: 400'), { status: 400 });
    expect(formatTreasuryOpError(err)).toBe('Operation failed.');
  });

  it('falls back for non-Error values', () => {
    expect(formatTreasuryOpError(null)).toBe('Operation failed.');
  });
});
