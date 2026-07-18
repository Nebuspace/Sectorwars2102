// @vitest-environment jsdom
/**
 * ContractBoardVenue — bulk_procurement posting (WO-CONTRACT-5-CLIENT-
 * SURFACE P1). Split out of ContractBoardVenue.test.tsx into its own file
 * DELIBERATELY: submitting the Post form requires a real, selectable
 * `<option>` in the Commodity <select>, which needs `useResourceCatalog`'s
 * catalog to actually resolve. `services/resourceCatalog.ts` caches via a
 * MODULE-LEVEL singleton (`cachedCatalog`/`inFlight`) with no exported
 * reset hook — the very first `getResourceCatalog()` call in a test file
 * claims `inFlight` for that whole file's lifetime; the establish idiom
 * elsewhere (ContractBoardVenue.test.tsx / ConstructionVenue.catalog.
 * test.tsx) is to stub `resourceAPI.list` as PERMANENTLY pending so every
 * test shares one inert catalog state. Mixing a resolving test into that
 * file wedges `inFlight` on whichever test runs first, starving every
 * later test's own `mockResolvedValueOnce` regardless of file position.
 * A separate test file gets its own module registry (vitest's per-file
 * isolation), so this is the only test that ever calls
 * `getResourceCatalog()` in its own registry — safe to resolve.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGetBoard, mockGetMine, mockPost, mockGetClaimable, mockResourceList } = vi.hoisted(() => ({
  mockGetBoard: vi.fn(),
  mockGetMine: vi.fn(),
  mockPost: vi.fn(),
  mockGetClaimable: vi.fn(),
  mockResourceList: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  contractsAPI: {
    getBoard: mockGetBoard,
    getMine: mockGetMine,
    getContract: vi.fn(),
    accept: vi.fn(),
    complete: vi.fn(),
    abandon: vi.fn(),
    post: mockPost,
    cancel: vi.fn(),
    insure: vi.fn(),
  },
  storageAPI: {
    rentLocker: vi.fn(),
    deposit: vi.fn(),
    retrieve: vi.fn(),
    getClaimable: mockGetClaimable,
  },
  shipAPI: {
    getCurrentShip: vi.fn(),
  },
  resourceAPI: { list: mockResourceList },
}));

import ContractBoardVenue from '../ContractBoardVenue';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

const STATION_ID = 'station-alpha';
const EMPTY_MINE = { posted: [], accepted: [] };

const VENUE_PROPS = {
  stationId: STATION_ID,
  stationName: 'Alpha Station',
  credits: 10000,
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

const CATALOG = [
  {
    name: 'ore',
    label: 'Ore',
    icon: null,
    category: 'mineral',
    base_price: 10,
    price_range_min: 5,
    price_range_max: 15,
    is_storable: true,
  },
];

describe('ContractBoardVenue — bulk_procurement posting', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetBoard.mockReset();
    mockGetMine.mockReset();
    mockPost.mockReset();
    mockGetClaimable.mockReset();
    mockResourceList.mockReset();
    VENUE_PROPS.onCreditsSet = vi.fn();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const setInputValue = async (selector: string, value: string) => {
    const el = container.querySelector(selector) as HTMLInputElement;
    expect(el, `expected an input matching ${selector}`).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  const setSelectValue = async (selector: string, value: string) => {
    const el = container.querySelector(selector) as HTMLSelectElement;
    expect(el, `expected a select matching ${selector}`).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  };

  const clickButton = async (text: string) => {
    const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(text));
    expect(btn, `expected a button containing "${text}"`).toBeTruthy();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('the Contract Type select defaults to Cargo Delivery and sends no surprise value when left untouched', async () => {
    mockResourceList.mockResolvedValueOnce(CATALOG);
    mockGetBoard.mockResolvedValueOnce([]);
    mockGetMine.mockResolvedValueOnce(EMPTY_MINE);
    mockGetClaimable.mockResolvedValueOnce([]);

    await act(async () => {
      root.render(<ContractBoardVenue {...VENUE_PROPS} />);
    });
    await flush();
    await clickButton('Post Contract');
    await flush();

    const select = container.querySelector('select[aria-label="Contract Type"]') as HTMLSelectElement;
    expect(select).toBeTruthy();
    expect(select.value).toBe('cargo_delivery');
  });

  it('selecting Bulk Procurement and submitting sends contract_type: bulk_procurement in the POST /contracts body', async () => {
    mockResourceList.mockResolvedValueOnce(CATALOG);
    mockGetBoard.mockResolvedValueOnce([]);
    mockGetMine.mockResolvedValueOnce(EMPTY_MINE);
    mockGetClaimable.mockResolvedValueOnce([]);
    mockPost.mockResolvedValueOnce({
      id: 'contract-99',
      status: 'posted',
      escrow_amount: 1000,
      escrow_state: 'held',
      posted_at: '2026-07-17T00:00:00Z',
      acceptance_fee_pct: 2.0,
      credits: 9000,
    });
    // Post-success refetch (Promise.allSettled([fetchBoard(), fetchMine()])).
    mockGetBoard.mockResolvedValueOnce([]);
    mockGetMine.mockResolvedValueOnce(EMPTY_MINE);

    await act(async () => {
      root.render(<ContractBoardVenue {...VENUE_PROPS} />);
    });
    await flush();
    await flush(); // extra tick — getResourceCatalog's own .then() + listener notification

    await clickButton('Post Contract');
    await flush();

    await setSelectValue('select[aria-label="Contract Type"]', 'bulk_procurement');
    await setSelectValue('select[aria-label="Commodity"]', 'ore');
    await setInputValue('input[aria-label="Quantity"]', '100');
    await setInputValue('input[aria-label="Payment"]', '1000');

    // Local-time construction (not toISOString, which is UTC) so the
    // value round-trips correctly through `new Date(...)` regardless of
    // the test runner's own timezone.
    const future = new Date(Date.now() + 6 * 3600_000);
    const pad = (n: number) => String(n).padStart(2, '0');
    const localDeadline = `${future.getFullYear()}-${pad(future.getMonth() + 1)}-${pad(future.getDate())}T${pad(future.getHours())}:${pad(future.getMinutes())}`;
    await setInputValue('input[aria-label="Deadline"]', localDeadline);

    const submitBtn = container.querySelector('.cb-post-submit') as HTMLButtonElement;
    expect(submitBtn, 'expected the Post form submit button').toBeTruthy();
    expect(submitBtn.disabled).toBe(false);
    await act(async () => {
      submitBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    expect(mockPost).toHaveBeenCalledWith(
      expect.objectContaining({
        contract_type: 'bulk_procurement',
        commodity_type: 'ore',
        quantity: 100,
        payment: 1000,
      })
    );
  });
});
