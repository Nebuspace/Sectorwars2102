// @vitest-environment jsdom
/**
 * LEG-3721 Soft-ORDER — ExplorationSuggestionPanel TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetSuggestions = vi.fn();

vi.mock('../../../services/api', () => ({
  ariaExplorationAPI: {
    getSuggestions: (...args: unknown[]) => mockGetSuggestions(...args),
  },
}));

import ExplorationSuggestionPanel, {
  formatExplorationSuggestionError,
} from '../ExplorationSuggestionPanel';

const FALLBACK = 'Failed to load exploration suggestions';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('ExplorationSuggestionPanel TypeError densify (LEG-3721)', () => {
  it('formatExplorationSuggestionError falls back on TypeError network collapse', () => {
    const text = formatExplorationSuggestionError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatExplorationSuggestionError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatExplorationSuggestionError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatExplorationSuggestionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatExplorationSuggestionError(new Error('exploration_offline'))).toBe(
      'exploration_offline',
    );
  });

  it('formatExplorationSuggestionError surfaces 403/429 without raw status codes (LEG-4030)', () => {
    expect(formatExplorationSuggestionError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatExplorationSuggestionError(apiRequestError(403, 'exploration_denied'))).toBe(
      'exploration_denied',
    );
    expect(formatExplorationSuggestionError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatExplorationSuggestionError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatExplorationSuggestionError(apiRequestError(403))).not.toMatch(/TypeError/i);
    expect(formatExplorationSuggestionError(apiRequestError(403))).not.toMatch(/Network Error/i);
    expect(formatExplorationSuggestionError(apiRequestError(403))).not.toMatch(/\b403\b/);
  });
});

describe('ExplorationSuggestionPanel transport collapse densify (LEG-3721)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetSuggestions.mockReset();
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

  it('network rejection surfaces role=alert fallback without raw transport text', async () => {
    mockGetSuggestions.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<ExplorationSuggestionPanel />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('malformed JSON TypeError surfaces role=alert fallback without raw exception text', async () => {
    mockGetSuggestions.mockRejectedValue(
      new TypeError("Cannot read properties of undefined (reading 'suggestions')"),
    );

    await act(async () => {
      root.render(<ExplorationSuggestionPanel />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Cannot read properties/i);
  });

  it.each([
    ['HTTP 403', apiRequestError(403)],
    ['HTTP 429', apiRequestError(429)],
  ])('suggestions load rejection with %s surfaces densified role=alert copy (LEG-4030)', async (_label, err) => {
    mockGetSuggestions.mockRejectedValue(err);

    await act(async () => {
      root.render(<ExplorationSuggestionPanel />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).not.toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
