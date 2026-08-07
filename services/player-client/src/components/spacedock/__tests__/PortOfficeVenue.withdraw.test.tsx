// @vitest-environment jsdom
/**
 * PortOfficeVenue — vault withdraw money path (WO-TESTCOV-PLAYER-PORT-OFFICE-WITHDRAW).
 * Owner Console → Withdraw → withdrawTreasury(stationId, amount) + onCreditsSet.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getListing, getMyStations, withdrawTreasury, OWNER_ID } = vi.hoisted(() => {
  const ownerId = 'player-1';
  const owned = {
    owner_id: ownerId,
    owner_name: 'You',
    status: 'owned',
    is_listed: false,
    list_price: null,
    tax_rate: 0.1,
    treasury_balance: 5000,
    purchasable: true,
  };
  return {
    OWNER_ID: ownerId,
    getListing: vi.fn(async () => owned),
    getMyStations: vi.fn(async () => [
      { station_id: 'station-1', treasury_balance: 5000, tax_rate: 0.1 },
    ]),
    withdrawTreasury: vi.fn(async () => ({ remaining_credits: 105_000 })),
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: OWNER_ID },
    getListing,
    listStation: vi.fn(),
    placeOffer: vi.fn(),
    getMyStations,
    setStationTax: vi.fn(),
    withdrawTreasury,
    getTakeoverStatus: vi.fn(async () => ({})),
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn(),
  }),
}));

import PortOfficeVenue from '../PortOfficeVenue';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function setInputValue(input: HTMLInputElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
    setter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('PortOfficeVenue — vault withdraw money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onCreditsSet: (value: number) => void;

  beforeEach(() => {
    getListing.mockClear();
    getMyStations.mockClear();
    withdrawTreasury.mockClear();
    onCreditsSet = vi.fn<(value: number) => void>();
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

  it('withdraws from the station vault via withdrawTreasury', async () => {
    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={onCreditsSet}
          onBack={vi.fn<() => void>()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      t.textContent?.includes('Owner Console'),
    ) as HTMLButtonElement;
    expect(ownerTab).toBeTruthy();
    await act(async () => {
      ownerTab.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(
        container.querySelector('input[aria-label="Credits to withdraw from the station vault"]'),
      ).toBeTruthy();
    });

    const input = container.querySelector(
      'input[aria-label="Credits to withdraw from the station vault"]',
    ) as HTMLInputElement;
    await setInputValue(input, '2500');

    const withdrawBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === 'Withdraw',
    ) as HTMLButtonElement;
    expect(withdrawBtn).toBeTruthy();
    expect(withdrawBtn.disabled).toBe(false);

    await act(async () => {
      withdrawBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(withdrawTreasury).toHaveBeenCalledWith('station-1', 2500);
    });
    expect(onCreditsSet).toHaveBeenCalledWith(105_000);
  });
});
