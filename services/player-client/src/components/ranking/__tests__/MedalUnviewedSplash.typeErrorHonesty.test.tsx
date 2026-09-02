// @vitest-environment jsdom
/**
 * LEG-3773 Soft-ORDER — MedalUnviewedSplash typeErrorHonesty.
 * LEG-4061 Soft-ORDER — HTTP 403/429 densify (invent=0).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getUnviewed = vi.fn();

vi.mock('../../../services/api', () => ({
  medalsAPI: {
    getUnviewed: (...args: unknown[]) => getUnviewed(...args),
  },
}));

import MedalUnviewedSplash from '../MedalUnviewedSplash';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('MedalUnviewedSplash transport collapse densify (LEG-3773)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getUnviewed.mockReset();
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

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('fetch %s rejection renders nothing and leaks no raw transport text', async (_label, err) => {
    getUnviewed.mockRejectedValue(err);

    await act(async () => {
      root.render(<MedalUnviewedSplash />);
    });
    await act(async () => {
      await flush();
    });

    expect(getUnviewed).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="medal-unviewed-splash"]')).toBeNull();
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});

describe('MedalUnviewedSplash 403/429 densify (LEG-4061)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getUnviewed.mockReset();
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

  it.each([
    ['HTTP 403', apiRequestError(403)],
    ['HTTP 429', apiRequestError(429)],
  ])('fetch %s rejection renders nothing and leaks no raw status/transport text', async (_label, err) => {
    getUnviewed.mockRejectedValue(err);

    await act(async () => {
      root.render(<MedalUnviewedSplash />);
    });
    await act(async () => {
      await flush();
    });

    expect(getUnviewed).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="medal-unviewed-splash"]')).toBeNull();
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(container.textContent).not.toMatch(/API Error/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
