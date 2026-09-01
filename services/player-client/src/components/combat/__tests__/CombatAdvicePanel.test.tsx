// @vitest-environment jsdom
/**
 * LEG-46 — CombatAdvicePanel Vitest.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetAdvice = vi.fn();

vi.mock('../../../services/api', () => ({
  ariaCombatAdviceAPI: {
    getAdvice: (...args: unknown[]) => mockGetAdvice(...args),
  },
}));

import CombatAdvicePanel, { formatCombatAdviceError } from '../CombatAdvicePanel';

describe('formatCombatAdviceError (LEG-46)', () => {
  it('maps fetch TypeError to stable fallback', () => {
    expect(formatCombatAdviceError(new TypeError('Failed to fetch'))).toBe(
      'ARIA combat advice unavailable',
    );
  });
});

describe('CombatAdvicePanel', () => {
  let container: HTMLElement;
  let root: Root;

  beforeEach(() => {
    mockGetAdvice.mockReset();
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

  it('renders combat advice summary', async () => {
    mockGetAdvice.mockResolvedValue({
      has_history: true,
      opponent_ship_type: 'CARGO_HAULER',
      summary: "You've fought Cargo Hauler 2 times: 1 win, 1 loss.",
      weapon_suggestion: 'Ship-type matchup favours you',
      encounters: 2,
      wins: 1,
      losses: 0,
    });

    await act(async () => {
      root.render(<CombatAdvicePanel opponentShipType="CARGO_HAULER" />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockGetAdvice).toHaveBeenCalledWith('CARGO_HAULER');
    expect(container.textContent).toContain('Cargo Hauler');
    expect(container.textContent).toContain('matchup favours you');
  });
});
