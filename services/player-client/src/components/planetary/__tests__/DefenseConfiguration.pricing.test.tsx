// @vitest-environment jsdom
/**
 * DefenseConfiguration — server-authoritative per-unit pricing
 * (WO-API-PHASE1 B3).
 *
 * The client no longer mirrors the ADR-0076 defense_unit_price formula (the
 * deleted CITADEL_MULT/PLANET_MOD tables); it fetches GET
 * /planets/{id}/defenses/pricing on mount and reads the server's own prices.
 * This proves: (1) the happy path renders the fetched per-unit prices and
 * gates Save on them, and (2) a failed/errored fetch degrades gracefully --
 * no crash, a clear "unavailable" message, Save blocked -- rather than
 * falling back to a guessed price that could understate the real charge.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Planet } from '../../../types/planetary';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetDefensePricing, mockUpdateDefenses, mockRefreshPlayerState, mockPlayerState } = vi.hoisted(() => ({
  mockGetDefensePricing: vi.fn(async () => ({ turrets: 380, shields: 900, fighters: 5000 })),
  mockUpdateDefenses: vi.fn(async () => ({
    success: true,
    defenses: { turrets: 10, shields: 0, drones: 0 },
  })),
  mockRefreshPlayerState: vi.fn(),
  mockPlayerState: { credits: 1000000 },
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      getDefensePricing: mockGetDefensePricing,
      updateDefenses: mockUpdateDefenses,
    },
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState, refreshPlayerState: mockRefreshPlayerState }),
}));

import { DefenseConfiguration } from '../DefenseConfiguration';

const PLANET: Planet = {
  id: 'planet-1',
  name: 'Test World',
  sectorId: '1',
  sectorName: 'Sol',
  planetType: 'TERRAN',
  colonists: 100,
  maxColonists: 1000,
  productionRates: { fuel: 10, organics: 10, equipment: 10, colonists: 1, research: 0 },
  allocations: { fuel: 30, organics: 30, equipment: 30, unused: 10 },
  buildings: [],
  defenses: { turrets: 0, shields: 0, drones: 0 },
  underSiege: false,
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('DefenseConfiguration — server-authoritative pricing', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetDefensePricing.mockClear();
    mockUpdateDefenses.mockClear();
    mockRefreshPlayerState.mockClear();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  const setSlider = async (index: number, value: string) => {
    const sliders = container.querySelectorAll('input.defense-slider');
    const el = sliders[index] as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  it('renders the server-fetched per-unit prices and gates Save on them', async () => {
    await act(async () => {
      root.render(<DefenseConfiguration planet={PLANET} />);
    });
    await flush();

    expect(mockGetDefensePricing).toHaveBeenCalledWith('planet-1');
    expect(container.textContent).toContain('380 cr / unit');
    expect(container.textContent).toContain('900 cr / unit');
    expect(container.textContent).toContain('5,000 cr / unit');

    // Bump turrets (index 0) from 0 -> 10: change cost = 10 * 380 = 3,800.
    await setSlider(0, '10');
    expect(container.textContent).toContain('Change cost:');
    expect(container.textContent).toContain('3,800');

    const saveBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Apply Changes'));
    expect(saveBtn?.disabled).toBe(false);

    await act(async () => {
      saveBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(mockUpdateDefenses).toHaveBeenCalledWith('planet-1', { turrets: 10, shields: 0, fighters: 0 });
  });

  it('degrades gracefully when the pricing fetch fails -- no crash, clear message, Save blocked', async () => {
    mockGetDefensePricing.mockRejectedValueOnce(new Error('403 Forbidden'));
    await act(async () => {
      root.render(<DefenseConfiguration planet={PLANET} />);
    });
    await flush();

    expect(container.textContent).toContain('Unable to load defense pricing');
    expect(container.textContent).toContain('price unavailable');

    // Make a change so Save's only remaining gate is the missing price.
    await setSlider(0, '10');

    const saveBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Apply Changes'));
    expect(saveBtn?.disabled).toBe(true);
    expect(saveBtn?.title).toMatch(/pricing unavailable/i);

    await act(async () => {
      saveBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(mockUpdateDefenses).not.toHaveBeenCalled();
  });
});
