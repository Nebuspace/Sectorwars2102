// @vitest-environment jsdom
/**
 * LEG-3238 Soft-ORDER — TradeCascadePanel DOM TypeError honesty.
 * LEG-3558 Soft-ORDER — Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('TradeCascadePanel TypeError honesty (LEG-3238)', () => {
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

  const expandAndSubmit = async () => {
    const header = container.querySelector('.trade-cascade-header') as HTMLElement;
    await act(async () => {
      header.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const form = container.querySelector('.trade-cascade-form') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('submit plan TypeError surfaces fallback alert without Failed to fetch / TypeError in DOM', async () => {
    mockPlanTradeCascade.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<TradeCascadePanel />);
    });
    await expandAndSubmit();

    const alert = container.querySelector('.trade-cascade-error[role="alert"]');
    expect(alert?.textContent).toBe('Failed to plan trade cascade.');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('submit plan Network Error surfaces fallback alert without Network Error in DOM (LEG-3558)', async () => {
    mockPlanTradeCascade.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<TradeCascadePanel />);
    });
    await expandAndSubmit();

    const alert = container.querySelector('.trade-cascade-error[role="alert"]');
    expect(alert?.textContent).toBe('Failed to plan trade cascade.');
    expect(container.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces 403/429 status paths and preserves server detail (LEG-3951)', () => {
    expect(formatTradeCascadeError(apiRequestError(403))).toBe(
      'Access denied — you cannot plan a trade cascade right now.',
    );
    expect(formatTradeCascadeError(apiRequestError(429))).toBe(
      'Trade cascade rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTradeCascadeError(apiRequestError(403, 'cascade_denied'))).toBe('cascade_denied');
    expect(formatTradeCascadeError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTradeCascadeError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });

  it('submit plan 403 surfaces access-denied copy without raw transport text in DOM (LEG-3951)', async () => {
    mockPlanTradeCascade.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<TradeCascadePanel />);
    });
    await expandAndSubmit();

    const alert = container.querySelector('.trade-cascade-error[role="alert"]');
    expect(alert?.textContent).toMatch(/Access denied/i);
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });

  it('submit plan 429 surfaces rate-limit copy without raw transport text in DOM (LEG-3951)', async () => {
    mockPlanTradeCascade.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(<TradeCascadePanel />);
    });
    await expandAndSubmit();

    const alert = container.querySelector('.trade-cascade-error[role="alert"]');
    expect(alert?.textContent).toMatch(/Trade cascade rate limit exceeded/i);
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
