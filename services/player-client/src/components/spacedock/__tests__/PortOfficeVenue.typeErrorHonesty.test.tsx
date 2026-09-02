// @vitest-environment jsdom
/**
 * LEG-3133 Soft-ORDER — PortOfficeVenue TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PortOfficeVenue, { formatPortOfficeVenueError } from '../PortOfficeVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getListing, getMyStations, getDefensePolicy, withdrawTreasury, OWNER_ID } = vi.hoisted(() => {
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
    getDefensePolicy: vi.fn(async () => ({})),
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
    getDefensePolicy,
    setDefensePolicy: vi.fn(),
    withdrawTreasury,
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
describe('formatPortOfficeVenueError TypeError densify (LEG-3133)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatPortOfficeVenueError(
      new TypeError('Failed to fetch'),
      'Vault withdrawal failed.',
    );
    expect(text).toBe('Vault withdrawal failed.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3274)', () => {
    expect(formatPortOfficeVenueError(new Error('Failed to fetch'), 'Vault withdrawal failed.')).toBe(
      'Vault withdrawal failed.',
    );
    expect(formatPortOfficeVenueError(new Error('Network Error'), 'Vault withdrawal failed.')).toBe(
      'Vault withdrawal failed.',
    );
    expect(formatPortOfficeVenueError(new Error('   '), 'Vault withdrawal failed.')).toBe(
      'Vault withdrawal failed.',
    );
  });

  it('preserves server detail for non-TypeError errors', () => {
    const err = Object.assign(new Error('insufficient treasury'), {
      response: { data: { detail: 'Treasury balance too low.' } },
    });
    expect(formatPortOfficeVenueError(err, 'Vault withdrawal failed.')).toBe(
      'Treasury balance too low.',
    );
  });
});

describe('PortOfficeVenue withdraw TypeError densify (LEG-3133)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getListing.mockClear();
    getMyStations.mockClear();
    getDefensePolicy.mockClear();
    withdrawTreasury.mockClear();
    getListing.mockResolvedValue({
      owner_id: OWNER_ID,
      owner_name: 'You',
      status: 'owned',
      is_listed: false,
      list_price: null,
      tax_rate: 0.1,
      treasury_balance: 5000,
      purchasable: true,
    });
    getDefensePolicy.mockResolvedValue({});
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

  it('withdraw TypeError surfaces fallback without Failed to fetch / TypeError', async () => {
    withdrawTreasury.mockRejectedValue(new TypeError('Failed to fetch'));

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
      t.textContent?.includes('Owner Console'),
    ) as HTMLButtonElement;
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
    await act(async () => {
      withdrawBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(withdrawTreasury).toHaveBeenCalled();
    });

    const alerts = Array.from(container.querySelectorAll('.genesis-error-message'));
    const withdrawAlert = alerts.find((el) => /Vault withdrawal failed/i.test(el.textContent ?? ''));
    expect(withdrawAlert).toBeTruthy();
    expect(withdrawAlert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(withdrawAlert?.textContent).not.toMatch(/TypeError/i);
  });
});

describe('formatPortOfficeVenueError 403/429 densify (LEG-4038)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    const fallback = 'Port office action failed.';
    expect(formatPortOfficeVenueError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatPortOfficeVenueError(apiRequestError(403, 'port_denied'), fallback)).toBe('port_denied');
    expect(formatPortOfficeVenueError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatPortOfficeVenueError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatPortOfficeVenueError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});
