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
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGetBoard, mockGetMine, mockAccept, mockComplete, mockAbandon, mockPost, mockCancel } = vi.hoisted(() => ({
  mockGetBoard: vi.fn(),
  mockGetMine: vi.fn(),
  mockAccept: vi.fn(),
  mockComplete: vi.fn(),
  mockAbandon: vi.fn(),
  mockPost: vi.fn(),
  mockCancel: vi.fn(),
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
    expect(findButton('Complete')).toBeTruthy();
    expect(findButton('Abandon')).toBeTruthy();
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
});
