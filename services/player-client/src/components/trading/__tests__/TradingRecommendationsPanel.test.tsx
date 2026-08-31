// @vitest-environment jsdom
/**
 * TradingRecommendationsPanel — LEG-3217 consumer of GET /api/v1/ai/recommendations.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockGetRecommendations, mockSubmitFeedback } = vi.hoisted(() => ({
  mockGetRecommendations: vi.fn(),
  mockSubmitFeedback: vi.fn(),
}));

vi.mock('../../../services/aiTradingService', () => ({
  default: {
    getRecommendations: mockGetRecommendations,
    submitRecommendationFeedback: mockSubmitFeedback,
  },
}));

import TradingRecommendationsPanel, {
  formatRecommendationsLoadError,
} from '../TradingRecommendationsPanel';

describe('formatRecommendationsLoadError (LEG-3217)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatRecommendationsLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to load trading recommendations');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error message when present', () => {
    expect(formatRecommendationsLoadError(new Error('Recommendations temporarily unavailable'))).toBe(
      'Recommendations temporarily unavailable',
    );
  });
});

describe('TradingRecommendationsPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetRecommendations.mockReset();
    mockSubmitFeedback.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const expandPanel = async () => {
    const header = container.querySelector('.trading-recommendations-header') as HTMLElement;
    await act(async () => {
      header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  it('shows honest empty state when ARIA returns no recommendations', async () => {
    mockGetRecommendations.mockResolvedValueOnce([]);

    await act(async () => {
      root.render(<TradingRecommendationsPanel />);
    });
    await expandPanel();

    expect(mockGetRecommendations).toHaveBeenCalledWith(10, false);
    const empty = container.querySelector('[data-testid="trading-recommendations-empty"]');
    expect(empty?.textContent).toMatch(/no active trading recommendations/i);
  });

  it('surfaces load failure without raw fetch errors', async () => {
    mockGetRecommendations.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<TradingRecommendationsPanel />);
    });
    await expandPanel();

    const error = container.querySelector('[data-testid="trading-recommendations-error"]');
    expect(error?.textContent).toBe('Failed to load trading recommendations');
    expect(error?.textContent).not.toMatch(/Failed to fetch/i);
  });

  it('renders populated recommendations when API returns rows', async () => {
    mockGetRecommendations.mockResolvedValueOnce([
      {
        id: 'rec-1',
        type: 'buy',
        confidence: 0.82,
        risk_level: 'low',
        reasoning: 'Ore is underpriced at this port.',
        priority: 3,
        expires_at: '2026-09-01T00:00:00Z',
        expected_profit: 4200,
        commodity_id: 'ore',
      },
    ]);

    await act(async () => {
      root.render(<TradingRecommendationsPanel />);
    });
    await expandPanel();

    expect(container.querySelector('[data-testid="recommendation-rec-1"]')).toBeTruthy();
    expect(container.textContent).toMatch(/Ore is underpriced/);
  });
});
