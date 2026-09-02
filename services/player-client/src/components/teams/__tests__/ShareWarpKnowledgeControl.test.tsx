// @vitest-environment jsdom
/**
 * ShareWarpKnowledgeControl — LEG-4118 team warp-knowledge catch-up.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const shareWarpKnowledge = vi.fn();

vi.mock('../../../services/api', () => ({
  teamAPI: {
    shareWarpKnowledge: (...args: unknown[]) => shareWarpKnowledge(...args),
  },
}));

import ShareWarpKnowledgeControl, {
  formatShareWarpKnowledgeError,
} from '../ShareWarpKnowledgeControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatShareWarpKnowledgeError (LEG-4118)', () => {
  const fallback = 'Warp knowledge share failed';

  it('densifies TypeError without transport strings', () => {
    expect(formatShareWarpKnowledgeError(new TypeError('Failed to fetch'), fallback)).toBe(
      fallback,
    );
    expect(
      formatShareWarpKnowledgeError(new TypeError('Failed to fetch'), fallback),
    ).not.toMatch(/TypeError/i);
  });

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatShareWarpKnowledgeError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatShareWarpKnowledgeError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(formatShareWarpKnowledgeError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatShareWarpKnowledgeError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});

describe('ShareWarpKnowledgeControl', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    shareWarpKnowledge.mockReset();
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

  it('shares successfully and surfaces tip counts', async () => {
    shareWarpKnowledge.mockResolvedValue({
      shared_warp_count: 3,
      recipient_count: 2,
      rows_created: 5,
    });

    await act(async () => {
      root.render(<ShareWarpKnowledgeControl teamId="team-1" />);
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="share-warp-knowledge-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(shareWarpKnowledge).toHaveBeenCalledWith('team-1');
    const text = container.querySelector('[data-testid="share-warp-knowledge-msg"]')?.textContent;
    expect(text).toMatch(/3 warp/i);
    expect(text).toMatch(/2 teammate/i);
    expect(text).toMatch(/5 new knowledge/i);
  });

  it('surfaces honest empty state when no warps known', async () => {
    shareWarpKnowledge.mockResolvedValue({
      shared_warp_count: 0,
      recipient_count: 0,
      rows_created: 0,
    });

    await act(async () => {
      root.render(<ShareWarpKnowledgeControl teamId="team-1" />);
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="share-warp-knowledge-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="share-warp-knowledge-msg"]')?.textContent;
    expect(text).toMatch(/No known warps/i);
  });

  it('surfaces 403 with player-safe copy', async () => {
    shareWarpKnowledge.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<ShareWarpKnowledgeControl teamId="team-1" />);
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="share-warp-knowledge-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="share-warp-knowledge-msg"]')?.textContent;
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('surfaces 429 with player-safe copy', async () => {
    shareWarpKnowledge.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(<ShareWarpKnowledgeControl teamId="team-1" />);
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="share-warp-knowledge-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="share-warp-knowledge-msg"]')?.textContent;
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
  });
});
