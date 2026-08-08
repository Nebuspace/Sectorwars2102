// @vitest-environment jsdom
/**
 * ConstructionVenue — pay-milestone money path (WO-TESTCOV-PLAYER-CONSTRUCTION-MILESTONE).
 * My Builds → Pay on first unpaid milestone → POST .../reservations/:id/pay-milestone.
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

const RESERVATION = {
  id: 'res-1',
  state: 'frame_assembly',
  ship_type: 'scout',
  ship_name: 'My Scout',
  station_id: 'station-1',
  credits_paid: 6000,
  milestones: {
    deposit: { amount: 1000, paid: true },
    keel_laid: { amount: 1500, paid: true },
    hull_complete: { amount: 2500, paid: true },
    final: { amount: 4000, paid: false },
  },
  resources_required: { ore: 100, equipment: 50, organics: 20 },
  resources_delivered: { ore: 100, equipment: 50, organics: 20 },
};

describe('ConstructionVenue — pay-milestone money path', () => {
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
        return { ok: true, json: async () => ({ quotes: [] }) };
      }
      if (u.includes('/construction/reservations/mine')) {
        return { ok: true, json: async () => ({ reservations: [RESERVATION] }) };
      }
      if (u.includes('/pay-milestone') && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({ remaining_credits: 96_000 }),
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

  it('posts pay-milestone when Pay is clicked on the first unpaid milestone', async () => {
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

    const buildsTab = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('My Builds'),
    ) as HTMLButtonElement;
    expect(buildsTab).toBeTruthy();
    await act(async () => {
      buildsTab.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.cr-milestone-pay-btn')).toBeTruthy();
    });

    const payBtn = container.querySelector('.cr-milestone-pay-btn') as HTMLButtonElement;
    expect(payBtn.textContent).toContain('Pay');
    expect(payBtn.disabled).toBe(false);

    await act(async () => {
      payBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/pay-milestone') && c[1]?.method === 'POST',
      );
      expect(post).toBeTruthy();
    });

    const call = fetchMock.mock.calls.find(
      (c) => String(c[0]).includes('/pay-milestone') && c[1]?.method === 'POST',
    )!;
    expect(String(call[0])).toContain('/api/v1/construction/reservations/res-1/pay-milestone');
    const [, init] = call;
    expect(JSON.parse(init?.body as string)).toEqual({ milestone: 'final' });
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    expect(onCreditsSet).toHaveBeenCalledWith(96_000);
  });
});
