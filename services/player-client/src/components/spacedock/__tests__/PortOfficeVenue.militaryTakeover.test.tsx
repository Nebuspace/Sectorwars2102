// @vitest-environment jsdom
/**
 * PortOfficeVenue — military takeover declare / siege / occupy (LEG-368).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ID = 'player-1';
const CHALLENGER_ID = 'player-2';

const {
  getListing,
  getMyStations,
  getDefensePolicy,
  getTakeoverStatus,
  militaryTakeover,
} = vi.hoisted(() => ({
  getListing: vi.fn(),
  getMyStations: vi.fn(),
  getDefensePolicy: vi.fn(),
  getTakeoverStatus: vi.fn(),
  militaryTakeover: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: CHALLENGER_ID },
    getListing,
    listStation: vi.fn(),
    placeOffer: vi.fn(),
    getMyStations,
    setStationTax: vi.fn(),
    setPriceLever: vi.fn(),
    setDockingFee: vi.fn(),
    setServiceCharge: vi.fn(),
    setStorageRental: vi.fn(),
    withdrawTreasury: vi.fn(),
    getDefensePolicy,
    setDefensePolicy: vi.fn(),
    getTakeoverStatus,
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn(),
    activateTariffCut: vi.fn(),
    activateCounterTrade: vi.fn(),
    activateFriendlyTrade: vi.fn(),
    setFeeDistribution: vi.fn(),
    militaryTakeover,
  }),
}));

import PortOfficeVenue from '../PortOfficeVenue';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const foreignOwnedListing = {
  owner_id: OWNER_ID,
  owner_name: 'Rival',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 0,
};

const myOwnedListing = {
  owner_id: CHALLENGER_ID,
  owner_name: 'You',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 5000,
};

const unownedListing = {
  owner_id: null,
  owner_name: null,
  status: 'unowned',
  is_listed: true,
  tax_rate: 0,
  treasury_balance: 0,
};

async function openWarRoom(container: HTMLElement) {
  const warTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
    (t.textContent || '').toLowerCase().includes('war'),
  ) as HTMLElement | undefined;
  expect(warTab).toBeTruthy();
  await act(async () => {
    warTab!.click();
    await flush();
    await flush();
  });
}

describe('PortOfficeVenue — military takeover', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getListing.mockReset();
    getMyStations.mockReset();
    getDefensePolicy.mockReset();
    getTakeoverStatus.mockReset();
    militaryTakeover.mockReset();
    getMyStations.mockResolvedValue([]);
    getDefensePolicy.mockResolvedValue({ defense_policy: {} });
    getTakeoverStatus.mockResolvedValue({});
    militaryTakeover.mockResolvedValue({
      campaign_type: 'military',
      status: 'building',
      siege_begins_at: '2099-01-01T00:00:00+00:00',
      defenders_remaining: 10,
    });
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

  it('shows declare/siege/occupy for challenger on foreign-owned station', async () => {
    getListing.mockResolvedValue(foreignOwnedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Frontier Dock"
          credits={50_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });

    await openWarRoom(container);

    const panel = container.querySelector('[data-testid="po-military-takeover"]');
    expect(panel).toBeTruthy();
    expect(container.querySelector('[data-testid="po-military-declare"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="po-military-siege"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="po-military-occupy"]')).toBeTruthy();
  });

  it('hides military panel for owner (non-eligible)', async () => {
    getListing.mockResolvedValue(myOwnedListing);
    getMyStations.mockResolvedValue([
      { station_id: 'station-1', treasury_balance: 5000, tax_rate: 0.1 },
    ]);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="My Port"
          credits={50_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });

    await openWarRoom(container);

    expect(container.querySelector('[data-testid="po-military-takeover"]')).toBeNull();
  });

  it('hides military panel when station is unowned', async () => {
    getListing.mockResolvedValue(unownedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Open Dock"
          credits={50_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });

    await openWarRoom(container);

    expect(container.querySelector('[data-testid="po-military-takeover"]')).toBeNull();
  });

  it('posts declare with tip MilitaryActionRequest action shape', async () => {
    getListing.mockResolvedValue(foreignOwnedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Frontier Dock"
          credits={50_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });

    await openWarRoom(container);

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-military-declare"]')!.click();
      await flush();
      await flush();
    });

    expect(militaryTakeover).toHaveBeenCalledWith('station-1', 'declare');
  });
});
