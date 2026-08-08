// @vitest-environment jsdom
/**
 * MedalUnviewedSplash — WO-WIRE-MEDALS-UNVIEWED-SPLASH.
 * Pins one-shot GET /api/v1/medals/unviewed on mount + render of awards;
 * empty / error → render nothing.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getUnviewed = vi.fn();

vi.mock('../../../services/api', () => ({
  medalsAPI: {
    getUnviewed: (...args: unknown[]) => getUnviewed(...args),
  },
}));

import MedalUnviewedSplash from '../MedalUnviewedSplash';

describe('MedalUnviewedSplash', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getUnviewed.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('fetches unviewed medals on mount and renders awards', async () => {
    getUnviewed.mockResolvedValue({ unviewed: ['first_blood', 'trade_star'] });

    await act(async () => {
      root.render(<MedalUnviewedSplash />);
    });

    expect(getUnviewed).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
    });

    const splash = container.querySelector('[data-testid="medal-unviewed-splash"]');
    expect(splash).not.toBeNull();
    expect(splash?.textContent).toMatch(/2 MEDALS EARNED WHILE AWAY/i);
    expect(splash?.textContent).toContain('first_blood');
    expect(splash?.textContent).toContain('trade_star');
  });

  it('renders nothing when the unviewed queue is empty', async () => {
    getUnviewed.mockResolvedValue({ unviewed: [] });

    await act(async () => {
      root.render(<MedalUnviewedSplash />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(getUnviewed).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="medal-unviewed-splash"]')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('renders nothing when the fetch fails', async () => {
    getUnviewed.mockRejectedValue(new Error('network'));

    await act(async () => {
      root.render(<MedalUnviewedSplash />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(getUnviewed).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="medal-unviewed-splash"]')).toBeNull();
  });
});
