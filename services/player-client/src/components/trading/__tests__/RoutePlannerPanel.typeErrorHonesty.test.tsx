// @vitest-environment jsdom
/**
 * LEG-3735 Soft-ORDER — RoutePlannerPanel TypeError/network densify.
 */
import { describe, expect, it } from 'vitest';
import { formatRouteHistoryError, formatRouteOptimizeError } from '../RoutePlannerPanel';

const OPTIMIZE_FALLBACK = 'Failed to optimize route.';
const HISTORY_FALLBACK = 'Failed to load recent plans.';

describe('RoutePlannerPanel TypeError densify (LEG-3735)', () => {
  it('formatRouteOptimizeError falls back on TypeError network collapse', () => {
    const text = formatRouteOptimizeError(new TypeError('Failed to fetch'));
    expect(text).toBe(OPTIMIZE_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatRouteHistoryError falls back on TypeError network collapse', () => {
    const text = formatRouteHistoryError(new TypeError('Failed to fetch'));
    expect(text).toBe(HISTORY_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('optimize/history fall back on axios Network Error / Failed to fetch', () => {
    expect(formatRouteOptimizeError(new Error('Network Error'))).toBe(OPTIMIZE_FALLBACK);
    expect(formatRouteOptimizeError(new Error('Failed to fetch'))).toBe(OPTIMIZE_FALLBACK);
    expect(formatRouteOptimizeError(new Error('Network Error'))).not.toMatch(/Network Error/i);

    expect(formatRouteHistoryError(new Error('Network Error'))).toBe(HISTORY_FALLBACK);
    expect(formatRouteHistoryError(new Error('Failed to fetch'))).toBe(HISTORY_FALLBACK);
    expect(formatRouteOptimizeError(new Error('sector unreachable'))).toBe('sector unreachable');
  });
});
