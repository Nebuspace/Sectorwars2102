// @vitest-environment jsdom
/**
 * LEG-3745 Soft-ORDER — RankDisplay TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetRank = vi.fn();

vi.mock('../../../services/api', () => ({
  rankingAPI: {
    getRank: (...args: unknown[]) => mockGetRank(...args),
  },
}));

import RankDisplay, { formatRankDisplayLoadError } from '../RankDisplay';

const FALLBACK = 'Failed to load rank info';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('RankDisplay TypeError densify (LEG-3745)', () => {
  it('formatRankDisplayLoadError falls back on TypeError network collapse', () => {
    const text = formatRankDisplayLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatRankDisplayLoadError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatRankDisplayLoadError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatRankDisplayLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('surfaces 403/429 status paths and preserves server detail', () => {
    expect(formatRankDisplayLoadError(apiRequestError(403))).toBe(
      'Access denied — you cannot view rank information right now.',
    );
    expect(formatRankDisplayLoadError(apiRequestError(429))).toBe(
      'Rank lookup rate limit exceeded — wait a moment and try again.',
    );
    expect(formatRankDisplayLoadError(apiRequestError(403, 'rank_panel_denied'))).toBe(
      'rank_panel_denied',
    );
    expect(formatRankDisplayLoadError(new Error('standings_unavailable'))).toBe(
      'standings_unavailable',
    );
  });
});

describe('RankDisplay load transport collapse densify (LEG-3745)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetRank.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('load Network Error surfaces honest fallback without raw transport text', async () => {
    mockGetRank.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<RankDisplay />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.rank-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
