// @vitest-environment jsdom
/**
 * LEG-3237 Soft-ORDER — TradingRecommendationsPanel DOM TypeError honesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetRecommendations } = vi.hoisted(() => ({
  mockGetRecommendations: vi.fn(),
}));

vi.mock('../../../services/aiTradingService', () => ({
  default: {
    getRecommendations: mockGetRecommendations,
    submitRecommendationFeedback: vi.fn(),
  },
}));

import TradingRecommendationsPanel, {
  formatRecommendationsLoadError,
} from '../TradingRecommendationsPanel';

describe('formatRecommendationsLoadError TypeError densify (LEG-3505)', () => {
  it('falls back on axios Network Error without leaking transport text', () => {
    const text = formatRecommendationsLoadError(new Error('Network Error'));
    expect(text).toBe('Failed to load trading recommendations');
    expect(text).not.toMatch(/Network Error/i);
  });
});

describe('TradingRecommendationsPanel TypeError honesty (LEG-3237)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetRecommendations.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('expand + getRecommendations TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    mockGetRecommendations.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<TradingRecommendationsPanel />);
    });

    const header = container.querySelector('.trading-recommendations-header') as HTMLElement;
    await act(async () => {
      header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const error = container.querySelector('[data-testid="trading-recommendations-error"]');
    expect(error?.textContent).toBe('Failed to load trading recommendations');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
