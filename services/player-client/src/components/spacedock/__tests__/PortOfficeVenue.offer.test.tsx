// @vitest-environment jsdom
/**
 * PortOfficeVenue — sealed-offer money path (WO-TESTCOV-PLAYER-PORT-OFFICE-OFFER).
 * File Sealed Offer → placeOffer(stationId, bid) + onCreditsSet from remaining_credits.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getListing, placeOffer, LISTED } = vi.hoisted(() => {
  const listed = {
    owner_id: 'rival-1',
    owner_name: 'Rival Co',
    status: 'owned',
    is_listed: true,
    list_price: 10_000,
    tax_rate: 0.1,
    treasury_balance: 0,
    purchasable: true,
  };
  return {
    LISTED: listed,
    getListing: vi.fn(async () => listed),
    placeOffer: vi.fn(async () => ({ remaining_credits: 90_000 })),
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: 'player-1' },
    getListing,
    listStation: vi.fn(),
    placeOffer,
    getMyStations: vi.fn(async () => []),
    setStationTax: vi.fn(),
    withdrawTreasury: vi.fn(),
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

describe('PortOfficeVenue — sealed offer money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onCreditsSet: (value: number) => void;

  beforeEach(() => {
    getListing.mockClear();
    placeOffer.mockClear();
    getListing.mockResolvedValue(LISTED);
    placeOffer.mockResolvedValue({ remaining_credits: 90_000 });
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

  it('files a sealed offer via placeOffer and applies remaining_credits', async () => {
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

    await vi.waitFor(() => {
      expect(container.querySelector('#po-bid-input')).toBeTruthy();
    });

    const input = container.querySelector('#po-bid-input') as HTMLInputElement;
    await setInputValue(input, '10000');

    const fileBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('File Sealed Offer'),
    ) as HTMLButtonElement;
    expect(fileBtn).toBeTruthy();
    expect(fileBtn.disabled).toBe(false);

    await act(async () => {
      fileBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(placeOffer).toHaveBeenCalledWith('station-1', 10_000);
    });
    expect(onCreditsSet).toHaveBeenCalledWith(90_000);
  });
});
