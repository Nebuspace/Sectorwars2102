// @vitest-environment jsdom
/**
 * TurnEconomyPage — low-turn scarcity warning + hint (WO-PROG-TURN-VISIBILITY).
 *
 * Canon (sw2102-docs FEATURES/gameplay/turns.md "Player-facing affordances"):
 * "Low-turn warning UI hints when the pool is below thresholds (design:
 * <50)." Pins the boundary on both sides — 49 (warning + hint) and 50 (no
 * warning, no hint) — and that the hint's "regen in Xh" reuses the exact
 * same remainingTurns/REGEN_PER_SEC math as the TIME TO FULL field (turns=49,
 * max_turns=1000 -> 951 remaining -> 951/(1000/86400)/3600 ≈ 22.8h -> rounds
 * to 23h).
 *
 * Mirrors Dashboard.icons.test.tsx's seam: jsdom + react-dom/client
 * createRoot + act(), no RTL.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

let mockPlayerState: Record<string, unknown> | null = null;
vi.mock('../../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState }),
}));

vi.mock('../../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ course: null, currentHopIndex: 0 }),
}));

import TurnEconomyPage from '../TurnEconomyPage';

const basePlayer = { credits: 500, max_turns: 1000 };

describe('TurnEconomyPage — low-turn warning', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    mockPlayerState = null;
  });

  it('tints the TURNS figure and shows the regen hint at 49 (below the <50 threshold)', async () => {
    mockPlayerState = { ...basePlayer, turns: 49 };
    await act(async () => {
      root.render(<TurnEconomyPage />);
    });

    expect(container.querySelector('.mfd-value-caution')?.textContent).toBe('49');
    const hint = container.querySelector('.mfd-page-cautionline');
    expect(hint).not.toBeNull();
    expect(hint!.textContent).toBe('low turns — regen in 23h');
  });

  it('shows neither the caution tint nor the hint at exactly 50 (the boundary is not low)', async () => {
    mockPlayerState = { ...basePlayer, turns: 50 };
    await act(async () => {
      root.render(<TurnEconomyPage />);
    });

    expect(container.querySelector('.mfd-value-caution')).toBeNull();
    expect(container.querySelector('.mfd-page-cautionline')).toBeNull();
  });

  it('shows the caution tint but no hint when max_turns is unknown (hint needs the cap for its math)', async () => {
    mockPlayerState = { credits: 500, turns: 10 };
    await act(async () => {
      root.render(<TurnEconomyPage />);
    });

    expect(container.querySelector('.mfd-value-caution')?.textContent).toBe('10');
    expect(container.querySelector('.mfd-page-cautionline')).toBeNull();
  });

  it('shows neither tint nor hint once the pool is full even below 50 turns headroom is moot (isFull guard)', async () => {
    mockPlayerState = { credits: 500, turns: 40, max_turns: 40 };
    await act(async () => {
      root.render(<TurnEconomyPage />);
    });

    // Below 50, so the pool figure itself still tints (scarcity is about the
    // absolute count) -- but the "regen in Xh" hint is suppressed once full,
    // matching the existing isFull guard on TIME TO FULL.
    expect(container.querySelector('.mfd-value-caution')?.textContent).toBe('40');
    expect(container.querySelector('.mfd-page-cautionline')).toBeNull();
  });
});
