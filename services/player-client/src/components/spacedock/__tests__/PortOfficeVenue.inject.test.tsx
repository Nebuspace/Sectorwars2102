// @vitest-environment jsdom
/**
 * PortOfficeVenue — vault inject money path (LEG-4123).
 * Owner Console → Inject → injectTreasury(stationId, amount) + onCreditsSet.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PortOfficeVenue, { formatPortOfficeVenueError } from '../PortOfficeVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getListing, getMyStations, injectTreasury, OWNER_ID } = vi.hoisted(() => {
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
    injectTreasury: vi.fn(async () => ({
      message: 'Injected 2,500 credits into Test Port',
      injected: 2500,
      treasury_balance: 7500,
      credits: 97_500,
    })),
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
    withdrawTreasury: vi.fn(),
    injectTreasury,
    getTakeoverStatus: vi.fn(async () => ({})),
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn(),
  }),
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function setInputValue(input: HTMLInputElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
    setter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatPortOfficeVenueError inject densify (LEG-4123)', () => {
  const fallback = 'Vault injection failed.';

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatPortOfficeVenueError(apiRequestError(403), fallback)).toMatch(/permission|not authorized|forbidden|do not have/i);
    expect(formatPortOfficeVenueError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(formatPortOfficeVenueError(apiRequestError(429), fallback)).toMatch(/rate limit|try again|wait/i);
    expect(formatPortOfficeVenueError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });

  it('densifies TypeError without transport strings', () => {
    expect(formatPortOfficeVenueError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatPortOfficeVenueError(new TypeError('Failed to fetch'), fallback)).not.toMatch(
      /TypeError/i,
    );
  });
});

describe('PortOfficeVenue — vault inject money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onCreditsSet: (value: number) => void;

  beforeEach(() => {
    getListing.mockClear();
    getMyStations.mockClear();
    injectTreasury.mockClear();
    injectTreasury.mockResolvedValue({
      message: 'Injected 2,500 credits into Test Port',
      injected: 2500,
      treasury_balance: 7500,
      credits: 97_500,
    });
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

  async function openOwnerVault() {
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
      expect(container.querySelector('[data-testid="po-vault-inject-input"]')).toBeTruthy();
    });
  }

  it('injects into the station vault via injectTreasury', async () => {
    await openOwnerVault();

    const input = container.querySelector(
      '[data-testid="po-vault-inject-input"]',
    ) as HTMLInputElement;
    await setInputValue(input, '2500');

    const injectBtn = container.querySelector(
      '[data-testid="po-vault-inject-btn"]',
    ) as HTMLButtonElement;
    expect(injectBtn.disabled).toBe(false);

    await act(async () => {
      injectBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(injectTreasury).toHaveBeenCalledWith('station-1', 2500);
    });
    expect(onCreditsSet).toHaveBeenCalledWith(97_500);
    expect(container.textContent).toMatch(/Injected 2,500 credits/i);
  });

  it('surfaces 403 inject errors with player-safe copy', async () => {
    injectTreasury.mockRejectedValue(apiRequestError(403));
    await openOwnerVault();

    const input = container.querySelector(
      '[data-testid="po-vault-inject-input"]',
    ) as HTMLInputElement;
    await setInputValue(input, '100');

    await act(async () => {
      (container.querySelector('[data-testid="po-vault-inject-btn"]') as HTMLButtonElement).click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/permission|not authorized|forbidden|do not have/i);
    });
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(onCreditsSet).not.toHaveBeenCalled();
  });

  it('surfaces 429 inject errors with player-safe copy', async () => {
    injectTreasury.mockRejectedValue(apiRequestError(429));
    await openOwnerVault();

    const input = container.querySelector(
      '[data-testid="po-vault-inject-input"]',
    ) as HTMLInputElement;
    await setInputValue(input, '100');

    await act(async () => {
      (container.querySelector('[data-testid="po-vault-inject-btn"]') as HTMLButtonElement).click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/rate limit|try again|wait/i);
    });
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(onCreditsSet).not.toHaveBeenCalled();
  });
});
