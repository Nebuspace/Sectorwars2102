// @vitest-environment jsdom
/**
 * LEG-3426 Soft-ORDER — RegionTradeDockPanel Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { friendlyError } from '../RegionTradeDockPanel';

describe('RegionTradeDockPanel TypeError densify (LEG-3426)', () => {
  const loadFallback = 'TradeDock construction status unreachable. Try again.';

  it('friendlyError falls back on Failed to fetch / NetworkError collapse', () => {
    expect(friendlyError('Failed to fetch', loadFallback)).toBe(loadFallback);
    expect(friendlyError('NetworkError', loadFallback)).toBe(loadFallback);
    expect(friendlyError('Failed to fetch', loadFallback)).not.toMatch(/Failed to fetch/i);
  });

  it('friendlyError falls back on axios Network Error', () => {
    expect(friendlyError('Network Error', loadFallback)).toBe(loadFallback);
    expect(friendlyError('Network Error', loadFallback)).not.toMatch(/Network Error/i);
  });

  it('friendlyError collapses bare API Error status to honest copy', () => {
    expect(friendlyError('API Error: 403', loadFallback)).toBe(
      'You are not the owner of this region.',
    );
    expect(friendlyError('API Error: 500', loadFallback)).toBe(loadFallback);
  });

  it('preserves non-generic detail', () => {
    expect(friendlyError('treasury_insufficient_detail', loadFallback)).toBe(
      'treasury_insufficient_detail',
    );
  });
});
