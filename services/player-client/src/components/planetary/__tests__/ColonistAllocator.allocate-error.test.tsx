// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { formatColonistAllocateError } from '../ColonistAllocator';

describe('formatColonistAllocateError (LEG-2876)', () => {
  it('preserves gameserver 400 detail', () => {
    const err = Object.assign(new Error('Planet not found or not owned by player'), {
      status: 400,
    });
    expect(formatColonistAllocateError(err)).toBe(
      'Planet not found or not owned by player',
    );
  });

  it('falls back when message is bare API Error: 400', () => {
    const err = Object.assign(new Error('API Error: 400'), { status: 400 });
    expect(formatColonistAllocateError(err)).toBe('Failed to update allocations');
  });

  it('falls back for non-Error values', () => {
    expect(formatColonistAllocateError('boom')).toBe('Failed to update allocations');
  });
});
