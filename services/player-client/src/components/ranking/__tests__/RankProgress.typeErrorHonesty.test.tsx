// @vitest-environment jsdom
/**
 * LEG-3681 Soft-ORDER — RankProgress TypeError/Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getProgress = vi.fn();

vi.mock('../../../services/api', () => ({
  rankingAPI: {
    getProgress: (...args: unknown[]) => getProgress(...args),
  },
}));

import RankProgress, { formatRankProgressLoadError } from '../RankProgress';

const FALLBACK = 'Failed to load rank progress';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('RankProgress TypeError densify (LEG-3681)', () => {
  it('formatRankProgressLoadError falls back on TypeError network collapse', () => {
    const text = formatRankProgressLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatRankProgressLoadError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatRankProgressLoadError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatRankProgressLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatRankProgressLoadError(new Error('rank_progress_denied'))).toBe('rank_progress_denied');
  });

  it('surfaces 403/429 status paths and preserves server detail (LEG-3822)', () => {
    expect(formatRankProgressLoadError(apiRequestError(403))).toBe(
      'Access denied — you cannot view rank progress right now.',
    );
    expect(formatRankProgressLoadError(apiRequestError(429))).toBe(
      'Rank progress rate limit exceeded — wait a moment and try again.',
    );
    expect(formatRankProgressLoadError(apiRequestError(403, 'rank_progress_denied'))).toBe(
      'rank_progress_denied',
    );
    expect(formatRankProgressLoadError(apiRequestError(403, 'API Error: 403'))).toBe(
      'Access denied — you cannot view rank progress right now.',
    );
    expect(formatRankProgressLoadError(apiRequestError(429, 'API Error: 429'))).toBe(
      'Rank progress rate limit exceeded — wait a moment and try again.',
    );
  });
});

describe('RankProgress load transport collapse densify (LEG-3681)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getProgress.mockReset();
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
    getProgress.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<RankProgress />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.rank-progress-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('load Failed to fetch (non-TypeError) surfaces honest fallback without raw transport text', async () => {
    getProgress.mockRejectedValue(new Error('Failed to fetch'));

    await act(async () => {
      root.render(<RankProgress />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.rank-progress-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
