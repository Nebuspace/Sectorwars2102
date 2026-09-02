// @vitest-environment jsdom
/**
 * LEG-3670 Soft-ORDER — Leaderboard TypeError/Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getPublicLeaderboard = vi.fn();

vi.mock('../../../services/api', () => ({
  rankingAPI: {
    getPublicLeaderboard: (...args: unknown[]) => getPublicLeaderboard(...args),
  },
}));

import Leaderboard, { formatLeaderboardLoadError } from '../Leaderboard';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const FALLBACK = 'Failed to load leaderboard';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('Leaderboard TypeError densify (LEG-3670)', () => {
  it('formatLeaderboardLoadError falls back on TypeError network collapse', () => {
    const text = formatLeaderboardLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatLeaderboardLoadError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatLeaderboardLoadError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatLeaderboardLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatLeaderboardLoadError(new Error('leaderboard_denied'))).toBe('leaderboard_denied');
  });
});

describe('Leaderboard 403/429 densify (LEG-3999)', () => {
  it('formatLeaderboardLoadError surfaces 403/429 without raw status codes', () => {
    expect(formatLeaderboardLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatLeaderboardLoadError(apiRequestError(403, 'leaderboard_denied'))).toBe(
      'leaderboard_denied',
    );
    expect(formatLeaderboardLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatLeaderboardLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatLeaderboardLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatLeaderboardLoadError(apiRequestError(403))).not.toMatch(/Network Error/i);
  });
});

describe('Leaderboard load transport collapse densify (LEG-3670)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getPublicLeaderboard.mockReset();
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
    getPublicLeaderboard.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<Leaderboard />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.leaderboard-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('load Failed to fetch (non-TypeError) surfaces honest fallback without raw transport text', async () => {
    getPublicLeaderboard.mockRejectedValue(new Error('Failed to fetch'));

    await act(async () => {
      root.render(<Leaderboard />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.leaderboard-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
