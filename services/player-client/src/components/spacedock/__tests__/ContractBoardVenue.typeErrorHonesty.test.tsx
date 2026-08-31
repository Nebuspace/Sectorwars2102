// @vitest-environment jsdom
/**
 * LEG-3139 Soft-ORDER — ContractBoardVenue TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ContractBoardVenue, { formatContractBoardVenueError } from '../ContractBoardVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetBoard, mockGetMine, mockGetClaimable, mockAccept } = vi.hoisted(() => ({
  mockGetBoard: vi.fn(),
  mockGetMine: vi.fn(),
  mockGetClaimable: vi.fn(),
  mockAccept: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  contractsAPI: {
    getBoard: mockGetBoard,
    getMine: mockGetMine,
    getContract: vi.fn(),
    accept: mockAccept,
    complete: vi.fn(),
    abandon: vi.fn(),
    post: vi.fn(),
    cancel: vi.fn(),
    insure: vi.fn(),
    dispute: vi.fn(),
  },
  storageAPI: {
    rentLocker: vi.fn(),
    deposit: vi.fn(),
    retrieve: vi.fn(),
    getClaimable: mockGetClaimable,
  },
  shipAPI: { getCurrentShip: vi.fn() },
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

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

describe('formatContractBoardVenueError TypeError densify (LEG-3139)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatContractBoardVenueError(
      new TypeError('Failed to fetch'),
      'The contract board terminal is not responding. Please try again.',
    );
    expect(text).toBe('The contract board terminal is not responding. Please try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(
      formatContractBoardVenueError(
        new Error('Board is closed for maintenance.'),
        'The contract board terminal is not responding. Please try again.',
      ),
    ).toBe('Board is closed for maintenance.');
  });
});

describe('ContractBoardVenue accept TypeError densify (LEG-3139)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetBoard.mockReset();
    mockGetMine.mockReset();
    mockGetClaimable.mockReset();
    mockAccept.mockReset();
    mockGetBoard.mockResolvedValue([CONTRACT_POSTED]);
    mockGetMine.mockResolvedValue({ posted: [], accepted: [] });
    mockGetClaimable.mockResolvedValue([]);
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

  it('accept TypeError surfaces fallback without Failed to fetch / TypeError', async () => {
    mockAccept.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(
        <ContractBoardVenue
          stationId={STATION_ID}
          stationName="Alpha Station"
          credits={10000}
          onCreditsSet={vi.fn()}
          onBack={vi.fn()}
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const acceptBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Accept'),
    ) as HTMLButtonElement;
    await act(async () => {
      acceptBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(mockAccept).toHaveBeenCalled();
    });

    const alert = container.querySelector('.genesis-error-message');
    expect(alert?.textContent).toMatch(/board rejected your acceptance/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });
});
