// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { formatDefenseUpdateError } from '../DefenseConfiguration';

describe('formatDefenseUpdateError (LEG-2878)', () => {
  it('preserves gameserver 400 detail', () => {
    const err = Object.assign(new Error('Planet not found or not owned by player'), {
      status: 400,
    });
    expect(formatDefenseUpdateError(err)).toBe(
      'Planet not found or not owned by player',
    );
  });

  it('falls back when message is bare API Error: 400', () => {
    const err = Object.assign(new Error('API Error: 400'), { status: 400 });
    expect(formatDefenseUpdateError(err)).toBe('Failed to update defenses');
  });

  it('falls back for non-Error values', () => {
    expect(formatDefenseUpdateError(undefined)).toBe('Failed to update defenses');
  });
});
