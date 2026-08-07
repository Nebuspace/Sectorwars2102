// @vitest-environment jsdom
/**
 * ConstructionVenue — reserve-slip money path (WO-TESTCOV-PLAYER-CONSTRUCTION-RESERVE).
 * Order Book → Reserve Slip → Confirm → POST /api/v1/construction/reservations.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../services/api', () => ({
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: { cargo: { contents: { ore: 500, equipment: 500, organics: 500 } } },
    refreshPlayerState: vi.fn(),
    loadShips: vi.fn(),
  }),
}));

import ConstructionVenue from '../ConstructionVenue';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const QUOTE = {
  ship_type: 'scout',
  total_cost: 10000,
  deposit: 2000,
  build_days: 3,
  resources_required: { ore: 100, equipment: 50, organics: 20 },
  requires_tier_a: false,
  uses_specialized_slip: false,
};

describe('ConstructionVenue — reserve slip money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;
  let onCreditsDelta: (delta: number) => void;
  let onCreditsSet: (value: number) => void;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'tok-test');
    onCreditsDelta = vi.fn<(delta: number) => void>();
    onCreditsSet = vi.fn<(value: number) => void>();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/construction/quotes')) {
        return { ok: true, json: async () => ({ quotes: [QUOTE] }) };
      }
      if (u.includes('/construction/reservations/mine')) {
        return { ok: true, json: async () => ({ reservations: [] }) };
      }
      if (u.includes('/construction/reservations') && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({ remaining_credits: 98_000, id: 'res-new' }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('posts construction/reservations when Confirm Deposit is clicked', async () => {
    await act(async () => {
      root.render(
        <ConstructionVenue
          stationId="station-1"
          stationName="Test Dock"
          tier="A"
          credits={100_000}
          onCreditsDelta={onCreditsDelta}
          onCreditsSet={onCreditsSet}
          onBack={vi.fn<() => void>()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain('Reserve Slip');
    });

    const reserve = container.querySelector('.cq-reserve-btn') as HTMLButtonElement;
    expect(reserve).toBeTruthy();
    await act(async () => {
      reserve.click();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.ship-confirm-panel, .confirm-actions')).toBeTruthy();
    });

    const primary = Array.from(container.querySelectorAll('.confirm-actions .action-button.primary')).find(
      (b) => b.textContent?.includes('Confirm Deposit'),
    ) as HTMLButtonElement;
    expect(primary).toBeTruthy();

    await act(async () => {
      primary.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/construction/reservations') && c[1]?.method === 'POST',
      );
      expect(post).toBeTruthy();
    });

    const call = fetchMock.mock.calls.find(
      (c) => String(c[0]).includes('/construction/reservations') && c[1]?.method === 'POST',
    )!;
    const [, init] = call;
    expect(JSON.parse(init?.body as string)).toEqual({
      station_id: 'station-1',
      ship_type: 'scout',
    });
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    expect(onCreditsDelta).toHaveBeenCalledWith(-2000);
  });
});
