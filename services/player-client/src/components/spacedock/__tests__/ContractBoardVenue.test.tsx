// @vitest-environment jsdom
/**
 * ContractBoardVenue — per-station trade contract board (WO-UIPC-CONTRACTS-BOARD).
 *
 * contractsAPI is mocked at the module boundary (not fetch), mirroring
 * RegionTradeDockPanel.test.tsx / ConstructionVenue.catalog.test.tsx. The
 * mocked shapes here mirror the REAL backend contract exactly (read
 * directly from gameserver's contracts.py / contract_service.py, not
 * guessed): GET /board returns a bare array, GET /mine returns
 * {posted, accepted}, and accept returns remaining_balance (not credits) —
 * see src/types/contract.ts.
 *
 * resourceAPI.list is stubbed to a never-resolving promise (matches
 * ConstructionVenue.catalog.test.tsx's technique) so useResourceCatalog
 * stays in its permanent-fallback state — getLabel/getIcon degrade to
 * their local default tables without ever needing a live catalog fetch,
 * which is the steady-state this UI actually renders under today.
 *
 * Pins:
 *  - the board tab renders a mocked GET /board row (commodity, quantity,
 *    payment, an Accept action)
 *  - accepting a board contract fires the accept API, feeds the new
 *    balance back through onCreditsSet, and the contract subsequently
 *    surfaces under My Contracts > Accepted (proven via re-fetched mocks,
 *    not a client-side splice — this venue is server-authoritative)
 *  - the Post Contract form's escrow preview is the live sum of payment +
 *    insurance reserve, computed client-side as the user types
 *  - WO-UI2-CANON-A-CONTRACTBOARD: both hand-rolled tablists (outer
 *    Board/My Contracts/Post Contract, nested Accepted/Posted) now render
 *    via the shared DeckPageTabs (components/cockpit/DeckPageTabs.tsx)
 *    with DISTINCT idBases ("cb" / "cb-mine") — no tab/panel id collides,
 *    both rails switch independently, and each is wired to its own
 *    role="tabpanel" region
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGetBoard,
  mockGetMine,
  mockAccept,
  mockComplete,
  mockAbandon,
  mockPost,
  mockCancel,
  mockRentLocker,
  mockDeposit,
  mockGetCurrentShip,
  mockInsure,
} = vi.hoisted(() => ({
  mockGetBoard: vi.fn(),
  mockGetMine: vi.fn(),
  mockAccept: vi.fn(),
  mockComplete: vi.fn(),
  mockAbandon: vi.fn(),
  mockPost: vi.fn(),
  mockCancel: vi.fn(),
  mockRentLocker: vi.fn(),
  mockDeposit: vi.fn(),
  mockGetCurrentShip: vi.fn(),
  mockInsure: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  contractsAPI: {
    getBoard: mockGetBoard,
    getMine: mockGetMine,
    getContract: vi.fn(),
    accept: mockAccept,
    complete: mockComplete,
    abandon: mockAbandon,
    post: mockPost,
    cancel: mockCancel,
    insure: mockInsure,
  },
  storageAPI: {
    rentLocker: mockRentLocker,
    deposit: mockDeposit,
    retrieve: vi.fn(),
  },
  shipAPI: {
    getCurrentShip: mockGetCurrentShip,
  },
  // Never resolves — useResourceCatalog stays in its permanent-fallback
  // state (mirrors ConstructionVenue.catalog.test.tsx).
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

import ContractBoardVenue from '../ContractBoardVenue';

// Two-microtask flush for the Promise.allSettled-based fetch-on-mount
// effect (fetchBoard + fetchMine run in parallel) — established idiom
// (RegionTradeDockPanel.test.tsx / CitadelManager.catalog.test.tsx).
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

const STATION_ID = 'station-alpha';
const DEADLINE_ISO = new Date(Date.now() + 6 * 3600_000).toISOString();

const CONTRACT_POSTED = {
  id: 'contract-1',
  issuer_type: 'npc',
  issuer_id: STATION_ID,
  acceptor_player_id: null,
  contract_type: 'cargo_delivery',
  status: 'posted',
  origin_station_id: null,
  destination_station_id: STATION_ID,
  commodity_type: 'ore',
  quantity: 50,
  payment: 2000,
  penalty: 2000,
  acceptance_fee_pct: 2.0,
  escrow_amount: null,
  escrow_state: null,
  faction_id: null,
  deadline: DEADLINE_ISO,
  posted_at: '2026-07-01T00:00:00Z',
  accepted_at: null,
  completed_at: null,
  insurance_coverage_tier: null,
  insurance_premium_paid: null,
  insurance_claim_filed: false,
};

const CONTRACT_ACCEPTED = {
  ...CONTRACT_POSTED,
  status: 'accepted',
  acceptor_player_id: 'player-1',
  accepted_at: '2026-07-10T00:00:00Z',
};

const EMPTY_MINE = { posted: [], accepted: [] };

const VENUE_PROPS = {
  stationId: STATION_ID,
  stationName: 'Alpha Station',
  credits: 10000,
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

describe('ContractBoardVenue', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetBoard.mockReset();
    mockGetMine.mockReset();
    mockAccept.mockReset();
    mockComplete.mockReset();
    mockAbandon.mockReset();
    mockPost.mockReset();
    mockCancel.mockReset();
    mockRentLocker.mockReset();
    mockDeposit.mockReset();
    mockGetCurrentShip.mockReset();
    mockInsure.mockReset();
    VENUE_PROPS.onCreditsSet = vi.fn();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const findButton = (text: string) =>
    Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(text));

  const clickButton = async (text: string) => {
    const btn = findButton(text);
    expect(btn, `expected a button containing "${text}"`).toBeTruthy();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const setInputValue = async (selector: string, value: string) => {
    const el = container.querySelector(selector) as HTMLInputElement;
    expect(el, `expected an input matching ${selector}`).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  it('renders a mocked GET /board row with commodity, quantity, payment, and an Accept action', async () => {
    mockGetBoard.mockResolvedValueOnce([CONTRACT_POSTED]);
    mockGetMine.mockResolvedValueOnce(EMPTY_MINE);

    await act(async () => {
      root.render(<ContractBoardVenue {...VENUE_PROPS} />);
    });
    await flush();

    expect(mockGetBoard).toHaveBeenCalledWith(STATION_ID);
    const text = container.textContent || '';
    expect(text).toContain('50'); // quantity
    expect(text).toContain('₡2,000'); // payment, formatCredits
    expect(findButton('Accept')).toBeTruthy();
  });

  it('accepting a board contract charges the fee, feeds the new balance to onCreditsSet, and the contract surfaces under My Contracts > Accepted', async () => {
    mockGetBoard.mockResolvedValueOnce([CONTRACT_POSTED]); // initial board
    mockGetMine.mockResolvedValueOnce(EMPTY_MINE); // initial mine
    mockAccept.mockResolvedValueOnce({
      id: CONTRACT_POSTED.id,
      status: 'accepted',
      acceptor_player_id: 'player-1',
      accepted_at: '2026-07-10T00:00:00Z',
      acceptance_fee_charged: 40,
      remaining_balance: 9960,
      deadline: DEADLINE_ISO,
    });
    mockGetBoard.mockResolvedValueOnce([]); // refetch after accept — contract left the board
    mockGetMine.mockResolvedValueOnce({ posted: [], accepted: [CONTRACT_ACCEPTED] }); // refetch after accept

    await act(async () => {
      root.render(<ContractBoardVenue {...VENUE_PROPS} />);
    });
    await flush();

    await clickButton('Accept');
    await flush();

    expect(mockAccept).toHaveBeenCalledWith(CONTRACT_POSTED.id);
    expect(VENUE_PROPS.onCreditsSet).toHaveBeenCalledWith(9960);

    // Board no longer shows the (now-accepted) contract's Accept button —
    // the server-authoritative refetch returned an empty board.
    expect(findButton('Accept')).toBeFalsy();

    await clickButton('My Contracts');
    await flush();

    const text = container.textContent || '';
    expect(text).toContain('ACCEPTED');
    expect(findButton('Deposit')).toBeTruthy();
    expect(findButton('Full delivery')).toBeTruthy();
    expect(findButton('Abandon')).toBeTruthy();
  });

  it('Deposit rents a locker and banks held cargo toward the contract (partial trip)', async () => {
    mockGetBoard.mockResolvedValueOnce([]);
    mockGetMine.mockResolvedValueOnce({ posted: [], accepted: [CONTRACT_ACCEPTED] });
    mockRentLocker.mockResolvedValueOnce({
      id: 'locker-1',
      contractId: CONTRACT_ACCEPTED.id,
      status: 'active',
    });
    mockGetCurrentShip.mockResolvedValueOnce({
      cargo: { contents: { ore: 20 }, used: 20, capacity: 50 },
    });
    mockDeposit.mockResolvedValueOnce({
      locker_id: 'locker-1',
      deposited: 20,
      accumulated: 20,
      quantity_required: 50,
      fee_charged: 0,
      completed: false,
      complete_result: null,
    });

    await act(async () => {
      root.render(<ContractBoardVenue {...VENUE_PROPS} />);
    });
    await flush();

    await clickButton('My Contracts');
    await flush();
    await clickButton('Deposit');
    await flush();

    expect(mockRentLocker).toHaveBeenCalledWith(CONTRACT_ACCEPTED.id);
    expect(mockGetCurrentShip).toHaveBeenCalled();
    expect(mockDeposit).toHaveBeenCalledWith('locker-1', 20);
    expect(container.textContent).toContain('Locker 20/50');
  });

  describe('insurance tier picker (WO-CONTRACT-1-INSURANCE)', () => {
    it('picking a tier and clicking Insure drives a REAL POST /insure with the selected tier, feeds the new balance to onCreditsSet, and the row flips to the Insured badge on refetch', async () => {
      mockGetBoard.mockResolvedValueOnce([]);
      mockGetMine.mockResolvedValueOnce({ posted: [], accepted: [CONTRACT_ACCEPTED] });
      mockInsure.mockResolvedValueOnce({
        id: CONTRACT_ACCEPTED.id,
        insurance_coverage_tier: 'hazard',
        insurance_premium_paid: 200,
        credits: 9800,
      });
      // Server-authoritative refetch after insure — the row now carries the tier.
      mockGetMine.mockResolvedValueOnce({
        posted: [],
        accepted: [{ ...CONTRACT_ACCEPTED, insurance_coverage_tier: 'hazard', insurance_premium_paid: 200 }],
      });

      await act(async () => {
        root.render(<ContractBoardVenue {...VENUE_PROPS} />);
      });
      await flush();

      await clickButton('My Contracts');
      await flush();

      // Default premium preview at the default 'basic' tier: 2% of 2000 = ₡40.
      expect(container.textContent).toContain('₡40');

      const select = container.querySelector('.cb-insure-tier') as HTMLSelectElement;
      expect(select, 'expected the insurance tier <select>').toBeTruthy();
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!;
        setter.call(select, 'hazard');
        select.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await flush();

      // Premium preview follows the selection: 10% of 2000 = ₡200.
      expect(container.textContent).toContain('₡200');

      await clickButton('Insure');
      await flush();

      expect(mockInsure).toHaveBeenCalledWith(CONTRACT_ACCEPTED.id, 'hazard');
      expect(VENUE_PROPS.onCreditsSet).toHaveBeenCalledWith(9800);
      expect(container.textContent).toContain('Insured');
      expect(container.textContent).toContain('Hazard (10%)');
      // The tier picker is gone now that the row is server-confirmed insured.
      expect(container.querySelector('.cb-insure-tier')).toBeFalsy();
    });

    it('an already-insured row renders the badge (no picker) from the initial fetch, with no insure call made', async () => {
      mockGetBoard.mockResolvedValueOnce([]);
      mockGetMine.mockResolvedValueOnce({
        posted: [],
        accepted: [{ ...CONTRACT_ACCEPTED, insurance_coverage_tier: 'standard', insurance_premium_paid: 100 }],
      });

      await act(async () => {
        root.render(<ContractBoardVenue {...VENUE_PROPS} />);
      });
      await flush();
      await clickButton('My Contracts');
      await flush();

      expect(container.textContent).toContain('Insured');
      expect(container.textContent).toContain('Standard (5%)');
      expect(container.querySelector('.cb-insure-tier')).toBeFalsy();
      expect(mockInsure).not.toHaveBeenCalled();
    });
  });

  it('the Post Contract form computes a live escrow preview as payment + insurance reserve', async () => {
    mockGetBoard.mockResolvedValueOnce([]);
    mockGetMine.mockResolvedValueOnce(EMPTY_MINE);

    await act(async () => {
      root.render(<ContractBoardVenue {...VENUE_PROPS} />);
    });
    await flush();

    await clickButton('Post Contract');
    await flush();

    // Before any input, the preview is the neutral zero-baseline.
    expect(container.querySelector('.cb-escrow-amount')?.textContent).toBe('₡0');

    await setInputValue('input[aria-label="Payment"]', '1000');
    await setInputValue('input[aria-label="Insurance reserve"]', '250');

    expect(container.querySelector('.cb-escrow-amount')?.textContent).toBe('₡1,250');
  });

  describe('DeckPageTabs migration (WO-UI2-CANON-A-CONTRACTBOARD)', () => {
    it('renders both tablists via DeckPageTabs (deck-tab-rail + venue skin class), with every tab/panel id unique across the two rails', async () => {
      mockGetBoard.mockResolvedValueOnce([]);
      mockGetMine.mockResolvedValueOnce({ posted: [], accepted: [CONTRACT_ACCEPTED] });

      await act(async () => {
        root.render(<ContractBoardVenue {...VENUE_PROPS} />);
      });
      await flush();

      // Outer rail: shared behavior class + this venue's own skin class,
      // both present on the same root (className passthrough, WO-UI2-
      // DECKTABS-CLASSNAME) — never a replacement of one for the other.
      const outerRail = container.querySelector('.cb-tabs');
      expect(outerRail).not.toBeNull();
      expect(outerRail?.classList.contains('deck-tab-rail')).toBe(true);
      expect(outerRail?.getAttribute('role')).toBe('tablist');

      await clickButton('My Contracts');
      await flush();

      const nestedRail = container.querySelector('.cb-mine-subtabs');
      expect(nestedRail).not.toBeNull();
      expect(nestedRail?.classList.contains('deck-tab-rail')).toBe(true);
      expect(nestedRail?.getAttribute('role')).toBe('tablist');
      // The nested rail is NOT the outer rail's element — two distinct
      // tablists are live at once (outer + nested-inside-My-Contracts).
      expect(nestedRail).not.toBe(outerRail);

      const allTabIds = Array.from(container.querySelectorAll('[role="tab"]')).map((t) => t.id);
      expect(allTabIds.length).toBe(5); // 3 outer + 2 nested
      expect(new Set(allTabIds).size).toBe(allTabIds.length); // no collisions
      expect(allTabIds).toEqual(
        expect.arrayContaining([
          'cb-tab-board',
          'cb-tab-mine',
          'cb-tab-post',
          'cb-mine-tab-accepted',
          'cb-mine-tab-posted',
        ])
      );
    });

    it('wires both content regions as aria-linked tabpanels, and the two rails switch independently', async () => {
      mockGetBoard.mockResolvedValueOnce([]);
      mockGetMine.mockResolvedValueOnce({ posted: [], accepted: [CONTRACT_ACCEPTED] });

      await act(async () => {
        root.render(<ContractBoardVenue {...VENUE_PROPS} />);
      });
      await flush();

      await clickButton('My Contracts');
      await flush();

      // Outer tabpanel: cb-panel-mine, aria-labelledby the outer active tab.
      const outerPanel = container.querySelector('.cb-content-area');
      expect(outerPanel?.getAttribute('role')).toBe('tabpanel');
      expect(outerPanel?.id).toBe('cb-panel-mine');
      expect(outerPanel?.getAttribute('aria-labelledby')).toBe('cb-tab-mine');

      // Nested tabpanel defaults to 'accepted'.
      let nestedPanel = container.querySelector('.cb-list');
      expect(nestedPanel?.getAttribute('role')).toBe('tabpanel');
      expect(nestedPanel?.id).toBe('cb-mine-panel-accepted');
      expect(nestedPanel?.getAttribute('aria-labelledby')).toBe('cb-mine-tab-accepted');
      expect(nestedPanel?.textContent).toContain('ACCEPTED');

      // Switching the NESTED rail only moves the nested tabpanel — the
      // outer tabpanel (still "My Contracts") is untouched, proving the
      // two rails are independently wired, not sharing state via id clash.
      await clickButton('Posted');
      await flush();

      expect(container.querySelector('.cb-content-area')?.id).toBe('cb-panel-mine');
      nestedPanel = container.querySelector('.cb-list');
      expect(nestedPanel?.id).toBe('cb-mine-panel-posted');
      expect(nestedPanel?.getAttribute('aria-labelledby')).toBe('cb-mine-tab-posted');
      expect(nestedPanel?.textContent).toContain("haven't posted any contracts yet");

      // Now switch the OUTER rail back to Board — the nested rail's own
      // last-selected subtab state is irrelevant here (Board has none).
      await clickButton('Board');
      await flush();

      expect(container.querySelector('.cb-content-area')?.id).toBe('cb-panel-board');
      expect(container.querySelector('.cb-content-area')?.getAttribute('aria-labelledby')).toBe('cb-tab-board');
      // The nested rail only exists inside the My Contracts panel.
      expect(container.querySelector('.cb-mine-subtabs')).toBeNull();
    });
  });
});
