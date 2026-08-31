// @vitest-environment jsdom
/**
 * LEG-3238 Soft-ORDER — TradeCascadePanel DOM TypeError honesty.
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

import TradeCascadePanel from '../TradeCascadePanel';

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
});
