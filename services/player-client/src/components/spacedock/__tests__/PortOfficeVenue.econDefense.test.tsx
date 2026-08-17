// @vitest-environment jsdom
/**
 * PortOfficeVenue — economic takeover defense + fee distribution (LEG-INI-35 / LEG-INI-36).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ID = 'player-1';
const OTHER_ID = 'player-2';

const {
  getListing,
  getMyStations,
  getDefensePolicy,
  activateTariffCut,
  activateCounterTrade,
  activateFriendlyTrade,
  setFeeDistribution,
  setPriceLever,
  setDockingFee,
  setServiceCharge,
  setStorageRental,
} = vi.hoisted(() => ({
  getListing: vi.fn(),
  getMyStations: vi.fn(),
  getDefensePolicy: vi.fn(),
  activateTariffCut: vi.fn(),
  activateCounterTrade: vi.fn(),
  activateFriendlyTrade: vi.fn(),
  setFeeDistribution: vi.fn(),
  setPriceLever: vi.fn(),
  setDockingFee: vi.fn(),
  setServiceCharge: vi.fn(),
  setStorageRental: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: OWNER_ID },
    getListing,
    listStation: vi.fn(),
    placeOffer: vi.fn(),
    getMyStations,
    setStationTax: vi.fn(),
    setPriceLever,
    setDockingFee,
    setServiceCharge,
    setStorageRental,
    withdrawTreasury: vi.fn(),
    getDefensePolicy,
    setDefensePolicy: vi.fn(),
    getTakeoverStatus: vi.fn(async () => ({})),
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn(),
    activateTariffCut,
    activateCounterTrade,
    activateFriendlyTrade,
    setFeeDistribution,
  }),
}));

import PortOfficeVenue from '../PortOfficeVenue';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const ownedListing = {
  owner_id: OWNER_ID,
  owner_name: 'You',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 5000,
};

const foreignListing = {
  owner_id: OTHER_ID,
  owner_name: 'Rival',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 0,
};

describe('PortOfficeVenue — economic defense + fee distribution', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getListing.mockReset();
    getMyStations.mockReset();
    getDefensePolicy.mockReset();
    activateTariffCut.mockReset();
    activateCounterTrade.mockReset();
    activateFriendlyTrade.mockReset();
    setFeeDistribution.mockReset();
    setPriceLever.mockReset();
    setDockingFee.mockReset();
    setServiceCharge.mockReset();
    setStorageRental.mockReset();
    getMyStations.mockResolvedValue([
      { station_id: 'station-1', treasury_balance: 5000, tax_rate: 0.1 },
    ]);
    getDefensePolicy.mockResolvedValue({
      station_id: 'station-1',
      defense_policy: {
        docking_access: 'open',
        hostility_list: [],
        punitive_fee_mult: 1.0,
        defender_posture: 'passive',
        drone_allocation_pct: 100,
        patrol_radius: 0,
      },
    });
    activateTariffCut.mockResolvedValue({ tax_rate: 0.05, prior_tax_rate: 0.1 });
    activateCounterTrade.mockResolvedValue({
      defense_volume: 12_000,
      cost: 12_000,
      remaining_credits: 88_000,
    });
    activateFriendlyTrade.mockResolvedValue({ defense_volume: 8_000 });
    setFeeDistribution.mockResolvedValue({
      defense_pct: 0.4,
      owner_pct: 0.3,
      operating_pct: 0.3,
    });
    setPriceLever.mockResolvedValue({ price_adjustment_lever: 0.05 });
    setDockingFee.mockResolvedValue({ docking_fee: 100, docking_fee_enabled: true });
    setServiceCharge.mockResolvedValue({ service_charge_multiplier: 1.2 });
    setStorageRental.mockResolvedValue({ storage_rental_per_day: 2500 });
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

  it('posts counter-trade / friendly-trade / tariff-cut with GS payload shapes', async () => {
    getListing.mockResolvedValue(ownedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });

    // Owner tab
    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    ) as HTMLElement | undefined;
    expect(ownerTab).toBeTruthy();
    await act(async () => {
      ownerTab!.click();
      await flush();
      await flush();
    });

    expect(container.querySelector('[data-testid="po-econ-defense"]')).toBeTruthy();

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-tariff-cut"]')!.click();
      await flush();
      await flush();
    });
    expect(activateTariffCut).toHaveBeenCalledWith('station-1');

    const volumeInput = container.querySelector<HTMLInputElement>(
      '[data-testid="po-counter-trade-volume"]',
    )!;
    await act(async () => {
      const native = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )!.set!;
      native.call(volumeInput, '12000');
      volumeInput.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-counter-trade"]')!.click();
      await flush();
      await flush();
    });
    expect(activateCounterTrade).toHaveBeenCalledWith('station-1', 12000);

    const friendlyVol = container.querySelector<HTMLInputElement>(
      '[data-testid="po-friendly-trade-volume"]',
    )!;
    const allyTeam = container.querySelector<HTMLInputElement>(
      '[data-testid="po-friendly-ally-team"]',
    )!;
    await act(async () => {
      const native = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )!.set!;
      native.call(friendlyVol, '8000');
      friendlyVol.dispatchEvent(new Event('input', { bubbles: true }));
      native.call(allyTeam, 'team-abc');
      allyTeam.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-friendly-trade"]')!.click();
      await flush();
      await flush();
    });
    expect(activateFriendlyTrade).toHaveBeenCalledWith('station-1', {
      contracted_volume: 8000,
      ally_team_id: 'team-abc',
      ally_faction: null,
    });
  });

  it('submits fee-distribution defense/owner split summing with operating 30%', async () => {
    getListing.mockResolvedValue(ownedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    ) as HTMLElement | undefined;
    await act(async () => {
      ownerTab!.click();
      await flush();
      await flush();
    });

    expect(container.querySelector('[data-testid="po-fee-distribution"]')).toBeTruthy();
    // Default defense 40% → owner 30%
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-fee-submit"]')!.click();
      await flush();
      await flush();
    });
    expect(setFeeDistribution).toHaveBeenCalledWith('station-1', 0.4, 0.3);
  });

  it('shows underfunding warning when defense_pct < 0.35', async () => {
    getListing.mockResolvedValue(ownedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    ) as HTMLElement | undefined;
    await act(async () => {
      ownerTab!.click();
      await flush();
      await flush();
    });

    const slider = container.querySelector<HTMLInputElement>('[data-testid="po-fee-defense-pct"]')!;
    await act(async () => {
      const native = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )!.set!;
      native.call(slider, '0.32');
      slider.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });
    expect(container.querySelector('[data-testid="po-fee-underfund-warn"]')).toBeTruthy();
  });

  it('hides economic-defense and fee panels for non-owners', async () => {
    getListing.mockResolvedValue(foreignListing);
    getMyStations.mockResolvedValue([]);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('[data-testid="po-econ-defense"]')).toBeNull();
    expect(container.querySelector('[data-testid="po-fee-distribution"]')).toBeNull();
    expect(container.querySelector('[data-testid="po-revenue-levers"]')).toBeNull();
    expect(activateTariffCut).not.toHaveBeenCalled();
    expect(setFeeDistribution).not.toHaveBeenCalled();
    expect(setPriceLever).not.toHaveBeenCalled();
  });

  it('posts revenue levers with tip GS payload shapes (LEG-366)', async () => {
    getListing.mockResolvedValue(ownedListing);

    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId="station-1"
          stationName="Test Port"
          credits={100_000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent || '').toLowerCase().includes('owner'),
    ) as HTMLElement | undefined;
    await act(async () => {
      ownerTab!.click();
      await flush();
      await flush();
    });

    expect(container.querySelector('[data-testid="po-revenue-levers"]')).toBeTruthy();

    const setInput = (el: HTMLInputElement, value: string) => {
      const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      native.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    };

    const priceSlider = container.querySelector<HTMLInputElement>(
      '[data-testid="po-price-lever-pct"]',
    )!;
    await act(async () => {
      setInput(priceSlider, '0.05');
      await flush();
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-price-lever-submit"]')!.click();
      await flush();
      await flush();
    });
    expect(setPriceLever).toHaveBeenCalledWith('station-1', 0.05);

    const dockingAmt = container.querySelector<HTMLInputElement>(
      '[data-testid="po-docking-fee-amount"]',
    )!;
    await act(async () => {
      setInput(dockingAmt, '100');
      await flush();
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="po-docking-fee-submit"]')!.click();
      await flush();
      await flush();
    });
    expect(setDockingFee).toHaveBeenCalledWith('station-1', 100, true);

    const svcMult = container.querySelector<HTMLInputElement>(
      '[data-testid="po-service-charge-mult"]',
    )!;
    await act(async () => {
      setInput(svcMult, '1.2');
      await flush();
    });
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="po-service-charge-submit"]')!
        .click();
      await flush();
      await flush();
    });
    expect(setServiceCharge).toHaveBeenCalledWith('station-1', 1.2);

    const storage = container.querySelector<HTMLInputElement>(
      '[data-testid="po-storage-rental-per-day"]',
    )!;
    await act(async () => {
      setInput(storage, '2500');
      await flush();
    });
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="po-storage-rental-submit"]')!
        .click();
      await flush();
      await flush();
    });
    expect(setStorageRental).toHaveBeenCalledWith('station-1', 2500);
  });
});
