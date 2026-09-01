// @vitest-environment jsdom
/**
 * LEG-3507 Soft-ORDER — ConstructionVenue reservations load network collapse densify.
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

const QUOTE = {
  ship_type: 'scout',
  total_cost: 10000,
  deposit: 2000,
  build_days: 3,
  resources_required: { ore: 100, equipment: 50, organics: 20 },
  requires_tier_a: false,
  uses_specialized_slip: false,
};

const VENUE_PROPS = {
  stationId: 'station-1',
  stationName: 'Test Dock',
  tier: 'A' as const,
  credits: 100000,
  onCreditsDelta: vi.fn(),
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

function mockFetchReservationsReject(rejectWith: () => unknown) {
  return vi.fn((url: string) => {
    if (url.includes('/construction/quotes')) {
      return Promise.resolve({ ok: true, json: async () => ({ quotes: [QUOTE] }) });
    }
    if (url.includes('/construction/reservations/mine')) {
      return Promise.reject(rejectWith());
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ConstructionVenue reservations load network collapse (LEG-3507)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  const renderAndOpenBuilds = async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const buildsTab = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('My Builds'),
    );
    expect(buildsTab).toBeTruthy();
    await act(async () => {
      buildsTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
      await flush();
    });
  };

  it('reservations TypeError surfaces Connection error fallback without transport leak', async () => {
    vi.stubGlobal('fetch', mockFetchReservationsReject(() => new TypeError('Failed to fetch')));

    await renderAndOpenBuilds();

    const alert = container.querySelector('.genesis-error-message');
    expect(alert?.textContent).toMatch(/Connection error\. Please try again\./i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('reservations axios-style Network Error surfaces Connection error fallback without transport leak', async () => {
    vi.stubGlobal('fetch', mockFetchReservationsReject(() => new Error('Network Error')));

    await renderAndOpenBuilds();

    const alert = container.querySelector('.genesis-error-message');
    expect(alert?.textContent).toMatch(/Connection error\. Please try again\./i);
    expect(container.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
