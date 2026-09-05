import { describe, it, expect } from 'vitest';
import { formatUniverseAdminError } from './universeAdminError';

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('formatUniverseAdminError (LEG-1213 invent=0 colonization)', () => {
  it('surfaces universe manage scope on 403', () => {
    expect(formatUniverseAdminError(axiosError(403), 'Failed to update')).toMatch(
      /admin\.universe\.manage|Access denied/i,
    );
  });

  it('surfaces admin rate-limit on 429', () => {
    expect(formatUniverseAdminError(axiosError(429), 'Failed to update')).toMatch(/rate limit/i);
  });

  it('keeps detail for non-scope failures', () => {
    expect(formatUniverseAdminError(axiosError(500, 'boom'), 'Failed to update')).toBe('boom');
  });

  it('uses fallback on TypeError/network collapse (LEG-3062)', () => {
    expect(
      formatUniverseAdminError(new TypeError('Failed to fetch'), 'Failed to save planet changes'),
    ).toBe('Failed to save planet changes');
  });

  it('uses fallback on TypeError/network collapse (LEG-3065)', () => {
    expect(
      formatUniverseAdminError(new TypeError('Failed to fetch'), 'Failed to load port data'),
    ).toBe('Failed to load port data');
  });

  it('uses fallback on TypeError/network collapse (LEG-3066)', () => {
    expect(
      formatUniverseAdminError(new TypeError('Failed to fetch'), 'Failed to update sector'),
    ).toBe('Failed to update sector');
  });

  it('uses fallback on axios-shaped Network Error (LEG-3578)', () => {
    expect(
      formatUniverseAdminError(new Error('Network Error'), 'Failed to save planet changes'),
    ).toBe('Failed to save planet changes');
  });

  it('uses fallback on Failed to fetch string Error (LEG-3578)', () => {
    expect(
      formatUniverseAdminError(new Error('Failed to fetch'), 'Failed to load port data'),
    ).toBe('Failed to load port data');
  });
});
