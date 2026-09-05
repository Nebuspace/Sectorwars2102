// @vitest-environment jsdom
/**
 * PortOfficeVenue — insolvency advance banner (LEG-4125).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PortOfficeVenue, {
  formatPortOfficeVenueError,
  normalizeMyStation,
} from '../PortOfficeVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ID = 'player-1';
const STATION_ID = 'station-1';
const SELL_AT = '2099-06-15T12:00:00+00:00';

const ownedListing = {
  owner_id: OWNER_ID,
  owner_name: 'You',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 5000,
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
  }),
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('normalizeMyStation insolvency hydrate (LEG-4125)', () => {
  it('maps tip revenue_summary insolvency fields', () => {
    const view = normalizeMyStation({
      station_id: STATION_ID,
      tax_rate: 0.1,
      treasury_balance: 1000,
      revenue: {
        insolvency_months: 3,
        insolvency_pending: true,
        insolvency_sell_at: SELL_AT,
      },
    });
    expect(view.insolvencyMonths).toBe(3);
    expect(view.insolvencyPending).toBe(true);
    expect(view.insolvencySellAt).toBe(SELL_AT);
  });

  it('accepts top-level twins when revenue nest is absent', () => {
    const view = normalizeMyStation({
      insolvency_months: 2,
      insolvency_pending: false,
      insolvency_sell_at: null,
    });
    expect(view.insolvencyMonths).toBe(2);
    expect(view.insolvencyPending).toBe(false);
    expect(view.insolvencySellAt).toBeNull();
  });
});

describe('PortOfficeVenue insolvency advance banner (LEG-4125)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getListing.mockResolvedValue(ownedListing);
    getMyStations.mockResolvedValue({
      stations: [
        {
          station_id: STATION_ID,
          tax_rate: 0.1,
          treasury_balance: 5000,
          revenue: {
            insolvency_months: 3,
            insolvency_pending: true,
            insolvency_sell_at: SELL_AT,
          },
        },
      ],
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

  async function openOwner() {
    await act(async () => {
      root.render(
        <PortOfficeVenue
          stationId={STATION_ID}
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
  }

  it('shows advance banner with tip sell-at when pending', async () => {
    await openOwner();
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="po-insolvency-advance-banner"]')).toBeTruthy();
    });
    expect(container.querySelector('[data-testid="po-insolvency-sell-at"]')?.textContent).toBe(
      SELL_AT,
    );
    expect(container.textContent).toMatch(/auto-sale/i);
    expect(container.querySelector('[data-testid="po-rescue-offer-marketplace"]')).toBeNull();
    expect(container.querySelector('[data-testid="po-compel-injection-vote"]')).toBeNull();
  });

  it('hides banner when insolvency is clear', async () => {
    getMyStations.mockResolvedValue({
      stations: [
        {
          station_id: STATION_ID,
          tax_rate: 0.1,
          treasury_balance: 5000,
          revenue: {
            insolvency_months: 0,
            insolvency_pending: false,
            insolvency_sell_at: null,
          },
        },
      ],
    });
    await openOwner();
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('[data-testid="po-insolvency-advance-banner"]')).toBeNull();
  });

  it('surfaces 403/429 hydrate failures with player-safe copy', async () => {
    getMyStations.mockRejectedValue(apiRequestError(403));
    await openOwner();
    await vi.waitFor(() => {
      expect(container.textContent).toMatch(/permission|holdings|ledger|answering/i);
    });
    expect(container.textContent).not.toMatch(/\b403\b/);

    getMyStations.mockRejectedValue(apiRequestError(429));
    const retry = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Retry'),
    ) as HTMLButtonElement | undefined;
    if (retry) {
      await act(async () => {
        retry.click();
        await flush();
        await flush();
      });
      await vi.waitFor(() => {
        expect(container.textContent).toMatch(/rate limit|wait|try again|ledger|holdings/i);
      });
      expect(container.textContent).not.toMatch(/\b429\b/);
    } else {
      // densify unit still covered below
      expect(formatPortOfficeVenueError(apiRequestError(429), 'Could not open your holdings ledger. Please try again.')).toMatch(
        /rate limit/i,
      );
    }
  });
});

describe('formatPortOfficeVenueError insolvency hydrate densify (LEG-4125)', () => {
  const fallback = 'Could not open your holdings ledger. Please try again.';

  it('densifies 403/429 without raw status codes', () => {
    expect(formatPortOfficeVenueError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatPortOfficeVenueError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(formatPortOfficeVenueError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatPortOfficeVenueError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
  });
});
