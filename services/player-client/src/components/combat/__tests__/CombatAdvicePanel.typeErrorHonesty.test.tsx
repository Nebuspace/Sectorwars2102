// @vitest-environment jsdom
/**
 * LEG-3729 Soft-ORDER — CombatAdvicePanel TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetAdvice = vi.fn();

vi.mock('../../../services/api', () => ({
  ariaCombatAdviceAPI: {
    getAdvice: (...args: unknown[]) => mockGetAdvice(...args),
  },
}));

import CombatAdvicePanel, { formatCombatAdviceError } from '../CombatAdvicePanel';

const FALLBACK = 'ARIA combat advice unavailable';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('CombatAdvicePanel TypeError densify (LEG-3729)', () => {
  it('formatCombatAdviceError falls back on TypeError network collapse', () => {
    const text = formatCombatAdviceError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatCombatAdviceError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatCombatAdviceError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatCombatAdviceError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for 503 and generic messages', () => {
    const err503 = Object.assign(new Error('advice_model_warming'), { status: 503 });
    expect(formatCombatAdviceError(err503)).toBe('advice_model_warming');
    expect(formatCombatAdviceError(new Error('opponent_unknown'))).toBe('opponent_unknown');
  });
});

describe('CombatAdvicePanel load transport collapse densify (LEG-3729)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetAdvice.mockReset();
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
    mockGetAdvice.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<CombatAdvicePanel opponentShipType="CARGO_HAULER" />);
    });
    await act(async () => {
      await flush();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
