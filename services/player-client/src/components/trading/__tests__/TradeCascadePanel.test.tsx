// @vitest-environment jsdom
/**
 * TradeCascadePanel — LEG-725 consumer of POST /api/v1/ai/trade-cascade.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockPlanTradeCascade } = vi.hoisted(() => ({
  mockPlanTradeCascade: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { current_sector_id: 'sector-42' },
  }),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    ariaTradeCascadeAPI: {
      planTradeCascade: mockPlanTradeCascade,
    },
  };
});

import TradeCascadePanel, { formatTradeCascadeError } from '../TradeCascadePanel';

describe('formatTradeCascadeError (LEG-2957)', () => {
  it('preserves gameserver detail when present', () => {
    const err = Object.assign(new Error('Start sector is unexplored'), { status: 400 });
    expect(formatTradeCascadeError(err)).toBe('Start sector is unexplored');
  });

  it('uses 403 fallback when detail is a bare API Error blob', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatTradeCascadeError(err)).toBe(
      'Access denied — you cannot plan a trade cascade right now.',
    );
  });

  it('uses 429 rate-limit copy', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatTradeCascadeError(err)).toBe(
      'Trade cascade rate limit exceeded — wait a moment and try again.',
    );
  });

  it('falls back for network-ish failures without status', () => {
    expect(formatTradeCascadeError(new Error('Failed to fetch'))).toBe('Failed to fetch');
    expect(formatTradeCascadeError(new Error('API Error: 500'))).toBe(
      'Failed to plan trade cascade.',
    );
  });

  it('falls back on TypeError network collapse (LEG-3057)', () => {
    const text = formatTradeCascadeError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to plan trade cascade\./i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('TradeCascadePanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockPlanTradeCascade.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const expandPanel = async () => {
    const header = container.querySelector('.trade-cascade-header') as HTMLElement;
    await act(async () => {
      header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('renders explored-space refusal without fabricating a plan', async () => {
    mockPlanTradeCascade.mockResolvedValueOnce({
      error: 'no_exploration_map',
      message: 'Explore more sectors to plan trade routes',
    });

    await act(async () => {
      root.render(<TradeCascadePanel />);
    });
    await expandPanel();

    const form = container.querySelector('.trade-cascade-form') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    await act(async () => {
      await Promise.resolve();
    });

    expect(mockPlanTradeCascade).toHaveBeenCalledWith({
      start_sector_id: 'sector-42',
      target_profit: 1000,
      max_jumps: 5,
    });
    expect(container.textContent).toContain('Explore more sectors to plan trade routes');
    expect(container.querySelector('.trade-cascade-result')).toBeNull();
  });

  it('renders a successful cascade plan with steps', async () => {
    mockPlanTradeCascade.mockResolvedValueOnce({
      cascade_id: 'c1',
      player_id: 'p1',
      total_profit: 1500,
      total_jumps: 2,
      profit_per_jump: 750,
      confidence: 0.85,
      steps: [
        {
          step: 1,
          sector: 's1',
          station: 'st1',
          action: 'buy',
          commodity: 'organics',
          expected_price: 100,
          confidence: 0.9,
          based_on: '5 observations',
        },
      ],
    });

    await act(async () => {
      root.render(<TradeCascadePanel />);
    });
    await expandPanel();

    const form = container.querySelector('.trade-cascade-form') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Total profit');
    expect(container.textContent).toContain('buy organics');
  });
});
