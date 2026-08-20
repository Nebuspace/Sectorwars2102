import { describe, it, expect } from 'vitest';
import {
  axiosResponseStatus,
  detailFromResponse,
  formatAdminApiError,
} from './adminApiError';

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : undefined },
  });

describe('adminApiError', () => {
  it('extracts response status', () => {
    expect(axiosResponseStatus(axiosError(403))).toBe(403);
    expect(axiosResponseStatus(new Error('network'))).toBeUndefined();
  });

  it('extracts string detail', () => {
    expect(detailFromResponse(axiosError(403, 'You lack admin.players.view'))).toBe(
      'You lack admin.players.view'
    );
  });

  it('403 uses body detail when present', () => {
    expect(
      formatAdminApiError(axiosError(403, 'You lack admin.ships.manage'), {
        fallback: 'Failed',
        scopeHint: 'admin.ships.manage',
      })
    ).toBe('You lack admin.ships.manage');
  });

  it('403 falls back to scopeHint when detail absent', () => {
    expect(
      formatAdminApiError(axiosError(403), {
        fallback: 'Failed',
        scopeHint: 'admin.players.view scope (PLAYERS_VIEW)',
      })
    ).toBe('Access denied — admin.players.view scope (PLAYERS_VIEW)');
  });

  it('429 shows rate-limit copy', () => {
    expect(
      formatAdminApiError(axiosError(429), { fallback: 'Failed' })
    ).toMatch(/rate limit/i);
  });

  it('404 uses notFoundMessage', () => {
    expect(
      formatAdminApiError(axiosError(404), {
        fallback: 'Failed',
        notFoundMessage: 'Catalog route not found on gameserver tip.',
      })
    ).toBe('Catalog route not found on gameserver tip.');
  });

  it('network error uses fallback', () => {
    expect(
      formatAdminApiError(new Error('Network Error'), { fallback: 'Gameserver unreachable' })
    ).toBe('Gameserver unreachable');
  });
});
