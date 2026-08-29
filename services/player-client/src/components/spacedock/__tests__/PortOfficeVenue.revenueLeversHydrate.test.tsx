// @vitest-environment jsdom
/**
 * PortOfficeVenue — revenue lever hydrate from my-stations (LEG-371).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ID = 'player-1';
const STATION_ID = 'station-1';

const ownedListing = {
  owner_id: OWNER_ID,
  owner_name: 'You',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 5000,
};

const hydratedMyStation = {
  station_id: STATION_ID,
  name: 'Test Port',
  tax_rate: 0.1,
  treasury_balance: 5000,
  price_adjustment_lever: 0.05,
  docking_fee: 200,
  docking_fee_enabled: false,
  service_charge_multiplier: 1.5,
  storage_rental_per_day: 2500,
};

const { getListing, getMyStations } = vi.hoisted(() => ({
  getListing: vi.fn(),
  getMyStations: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: OWNER_ID },
    getListing,
    listStation: vi.fn(),
    placeOffer: vi.fn(),
    getMyStations,
    setStationTax: vi.fn(),
    withdrawTreasury: vi.fn(),
    getTakeoverStatus: vi.fn(() => Promise.resolve({})),
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn(),
    getDefensePolicy: vi.fn(() => Promise.resolve({})),
    setPriceLever: vi.fn(),
    setDockingFee: vi.fn(),
    setServiceCharge: vi.fn(),
    setStorageRental: vi.fn(),
    setFeeDistribution: vi.fn(),
    activateTariffCut: vi.fn(),
    activateCounterTrade: vi.fn(),
    activateFriendlyTrade: vi.fn(),
  }),
}));

import PortOfficeVenue from '../PortOfficeVenue';

const VENUE_PROPS = {
  stationId: STATION_ID,
  stationName: 'Test Port',
  credits: 100000,
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('PortOfficeVenue — revenue lever hydrate (LEG-371)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getListing.mockResolvedValue(ownedListing);
    getMyStations.mockResolvedValue({ stations: [hydratedMyStation] });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('hydrates revenue lever controls from my-stations after owner tab opens', async () => {
    await act(async () => {
      root.render(<PortOfficeVenue {...VENUE_PROPS} />);
    });
    await flush();

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    ) as HTMLButtonElement | undefined;
    expect(ownerTab).toBeTruthy();
    await act(async () => {
      ownerTab.click();
    });
    await flush();

    expect(getMyStations).toHaveBeenCalled();

    const priceLever = container.querySelector(
      '[data-testid="po-price-lever-pct"]',
    ) as HTMLInputElement;
    expect(priceLever?.value).toBe('0.05');

    const dockingFee = container.querySelector(
      '[data-testid="po-docking-fee-amount"]',
    ) as HTMLInputElement;
    expect(dockingFee?.value).toBe('200');

    const dockingEnabled = container.querySelector(
      '[data-testid="po-docking-fee-enabled"]',
    ) as HTMLInputElement;
    expect(dockingEnabled?.checked).toBe(false);

    const serviceCharge = container.querySelector(
      '[data-testid="po-service-charge-mult"]',
    ) as HTMLInputElement;
    expect(serviceCharge?.value).toBe('1.5');

    const storageRental = container.querySelector(
      '[data-testid="po-storage-rental-per-day"]',
    ) as HTMLInputElement;
    expect(storageRental?.value).toBe('2500');
  });
});
