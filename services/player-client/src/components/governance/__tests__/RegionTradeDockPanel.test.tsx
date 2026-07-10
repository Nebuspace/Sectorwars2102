// @vitest-environment jsdom
/**
 * RegionTradeDockPanel — region-owner console for region-funded TradeDock
 * construction (WO-TD-RGF-1 FE lane).
 *
 * regionOwnerAPI / constructionAPI are mocked at the module boundary (not
 * fetch), mirroring RoutePlannerPanel.test.tsx / CitadelManager.catalog.
 * test.tsx. The mocked shapes here mirror the REAL backend contract (read
 * directly from regional_governance.py / construction_service.py, not
 * guessed): POST takes { station_id }, success is flat (no project/
 * active_project wrapper), and there is no dedicated status GET — active
 * projects are discovered via GET /construction/reservations/mine filtered
 * to ship_type 'TRADEDOCK_CONSTRUCTION'. Pins:
 *  - non-owner render is a strict no-op (never touches the DOM or leaks
 *    treasury numbers), independent of the caller-side gate in GameDashboard
 *  - the eligibility readout reflects sector count vs the canon 500-sector
 *    threshold, and treasury reads as "unverified" (not fabricated) since
 *    no live GET currently returns it
 *  - the confirm flow fires exactly one initiate POST, never on the first
 *    click, and is disabled until a syntactically valid station ID is entered
 *  - 402/403/404/409 rejections each render an honest, non-generic inline message
 *  - an active project (discovered via the reservations list) replaces the
 *    eligibility/action UI with progress, not a button
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGetMyRegion, mockGetMyReservations, mockGetReservation, mockInitiate } = vi.hoisted(() => ({
  mockGetMyRegion: vi.fn(),
  mockGetMyReservations: vi.fn(),
  mockGetReservation: vi.fn(),
  mockInitiate: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  regionOwnerAPI: {
    getMyRegion: mockGetMyRegion,
    initiateTradeDockConstruction: mockInitiate,
  },
  constructionAPI: {
    getMyReservations: mockGetMyReservations,
    getReservation: mockGetReservation,
  },
}));

import RegionTradeDockPanel from '../RegionTradeDockPanel';

// Two-microtask flush for a Promise.all-based fetch-on-mount effect —
// established idiom (see CitadelManager.catalog.test.tsx).
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

const VALID_STATION_ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6';
const ELIGIBLE_REGION = { total_sectors: 750 };
const NO_RESERVATIONS = { reservations: [] };

describe('RegionTradeDockPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetMyRegion.mockReset();
    mockGetMyReservations.mockReset();
    mockGetReservation.mockReset();
    mockInitiate.mockReset();
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  const enterStationId = async (value: string) => {
    const input = container.querySelector('.rtd-station-input') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  it('renders nothing for a non-owner, without ever calling any status API', async () => {
    await act(async () => {
      root.render(
        <RegionTradeDockPanel regionId="region-1" regionName="Test Region" isOwner={false} />
      );
    });
    await flush();

    expect(container.innerHTML).toBe('');
    expect(mockGetMyRegion).not.toHaveBeenCalled();
    expect(mockGetMyReservations).not.toHaveBeenCalled();
  });

  it('passes its regionId prop through to getMyRegion, never an unscoped probe (WO-DRIFT-admin-gov-multiregion-owner-500)', async () => {
    // This panel already knows which region it's scoped to (its regionId
    // prop, sourced from GameDashboard's ownership probe/switcher) — it must
    // never re-probe unscoped, which would 400 for a 2+-region owner instead
    // of reusing the already-resolved region.
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);

    await act(async () => {
      root.render(
        <RegionTradeDockPanel regionId="region-42" regionName="Test Region" isOwner={true} />
      );
    });
    await flush();

    expect(mockGetMyRegion).toHaveBeenCalledWith('region-42');
  });

  it('renders nothing when isOwner is true but no regionId is supplied yet', async () => {
    await act(async () => {
      root.render(<RegionTradeDockPanel regionId={null} isOwner={true} />);
    });
    await flush();

    expect(container.innerHTML).toBe('');
  });

  it('shows a met sector row, an "unverified" treasury row, and a station-gated INITIATE button', async () => {
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);

    await act(async () => {
      root.render(
        <RegionTradeDockPanel regionId="region-1" regionName="Test Region" isOwner={true} />
      );
    });
    await flush();

    const rows = Array.from(container.querySelectorAll('.rtd-eligibility-row'));
    expect(rows).toHaveLength(2);
    expect(rows[0].classList.contains('met')).toBe(true); // region size
    expect(rows[1].classList.contains('unknown')).toBe(true); // treasury — no live source yet

    // No station ID entered yet — button must stay disabled.
    let initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    expect(initiateBtn.disabled).toBe(true);

    await enterStationId(VALID_STATION_ID);

    initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    expect(initiateBtn.disabled).toBe(false);
  });

  it('renders a live treasury number as met when the server includes treasury_balance and it clears the threshold', async () => {
    mockGetMyRegion.mockResolvedValue({ total_sectors: 750, treasury_balance: 60_000_000 });
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();

    const rows = Array.from(container.querySelectorAll('.rtd-eligibility-row'));
    expect(rows[1].classList.contains('met')).toBe(true);
    expect(rows[1].classList.contains('unknown')).toBe(false);
    expect(rows[1].textContent).toContain('60,000,000');
  });

  it('visually flags an insufficient live treasury_balance but does NOT hard-gate the button on it — the server 402 stays authoritative', async () => {
    mockGetMyRegion.mockResolvedValue({ total_sectors: 750, treasury_balance: 10_000_000 });
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();
    await enterStationId(VALID_STATION_ID);

    const rows = Array.from(container.querySelectorAll('.rtd-eligibility-row'));
    expect(rows[1].classList.contains('unmet')).toBe(true);
    expect(rows[1].textContent).toContain('10,000,000');

    // Sector threshold + a valid station ID are still enough to enable the
    // button — an insufficient treasury is flagged visually, not gated
    // client-side; the server's 402 is the actual enforcement.
    const initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    expect(initiateBtn.disabled).toBe(false);
  });

  it('keeps INITIATE disabled below the sector threshold even with a valid station ID', async () => {
    mockGetMyRegion.mockResolvedValue({ total_sectors: 300 });
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();
    await enterStationId(VALID_STATION_ID);

    const rows = Array.from(container.querySelectorAll('.rtd-eligibility-row'));
    expect(rows[0].classList.contains('unmet')).toBe(true);

    const initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    expect(initiateBtn.disabled).toBe(true);
  });

  it('fires exactly one initiate POST with the entered station_id, through the confirm dialog', async () => {
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);
    mockInitiate.mockResolvedValue({
      message: 'Region-funded TradeDock construction initiated',
      reservation_id: 'res-1',
      station_id: VALID_STATION_ID,
      state: 'queued',
    });
    mockGetReservation.mockResolvedValue({
      id: 'res-1',
      station_id: VALID_STATION_ID,
      ship_type: 'TRADEDOCK_CONSTRUCTION',
      state: 'queued',
      created_at: new Date().toISOString(),
      overall_progress_percent: 0,
    });

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();
    await enterStationId(VALID_STATION_ID);

    const initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    await act(async () => {
      initiateBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // First click only arms the confirm dialog — no request yet.
    expect(mockInitiate).not.toHaveBeenCalled();
    const confirmBtn = container.querySelector('.rtd-btn.primary.commit') as HTMLButtonElement;
    expect(confirmBtn).not.toBeNull();
    expect(confirmBtn.textContent).toContain('CONFIRM CONSTRUCTION');

    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    expect(mockInitiate).toHaveBeenCalledTimes(1);
    expect(mockInitiate).toHaveBeenCalledWith(VALID_STATION_ID);
  });

  it.each([
    ['402', 'treasury'],
    ['403', 'not the owner'],
    ['404', 'Station not found'],
    ['409', 'already has a TradeDock construction'],
  ])('renders an honest inline message for a bare %s rejection', async (status, expectedSubstring) => {
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);
    mockInitiate.mockRejectedValue(new Error(`API Error: ${status}`));

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();
    await enterStationId(VALID_STATION_ID);

    const initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    await act(async () => {
      initiateBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const confirmBtn = container.querySelector('.rtd-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    const strip = container.querySelector('.rtd-validation-strip');
    expect(strip).not.toBeNull();
    expect(strip?.textContent).toContain(expectedSubstring);
    // Never surface the raw, unhelpful status-only fallback to the player.
    expect(strip?.textContent).not.toContain(`API Error: ${status}`);
  });

  it('passes through a server-supplied human detail message verbatim (e.g. the real 409 sector-count text)', async () => {
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue(NO_RESERVATIONS);
    mockInitiate.mockRejectedValue(
      new Error('Region-funded TradeDock construction requires >= 500 sectors; this region has 480.')
    );

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();
    await enterStationId(VALID_STATION_ID);

    const initiateBtn = container.querySelector('.rtd-btn.primary') as HTMLButtonElement;
    await act(async () => {
      initiateBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const confirmBtn = container.querySelector('.rtd-btn.primary.commit') as HTMLButtonElement;
    await act(async () => {
      confirmBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    expect(container.querySelector('.rtd-validation-strip')?.textContent).toContain(
      'this region has 480'
    );
  });

  it('discovers an active project from the reservations list and renders progress instead of the eligibility/action UI', async () => {
    const startedAt = new Date(Date.now() - 30 * 86400 * 1000).toISOString();
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue({
      reservations: [
        {
          id: 'res-1',
          station_id: VALID_STATION_ID,
          ship_type: 'TRADEDOCK_CONSTRUCTION',
          state: 'hull_complete',
          created_at: startedAt,
          overall_progress_percent: 33.3,
        },
        {
          id: 'res-0',
          station_id: 'other-station',
          ship_type: 'LIGHT_FREIGHTER',
          state: 'claimed',
          created_at: startedAt,
        },
      ],
    });

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();

    expect(container.querySelector('.rtd-progress-card')).not.toBeNull();
    expect(container.querySelector('.rtd-eligibility-list')).toBeNull();
    expect(container.querySelector('.rtd-btn.primary')).toBeNull();

    const fill = container.querySelector('.rtd-progress-fill') as HTMLElement;
    expect(parseFloat(fill.style.width)).toBeCloseTo(33.3, 1);
  });

  it('shows a distinct "awaiting activation" note for a completed-but-unclaimed project, matching the documented claim() gap', async () => {
    mockGetMyRegion.mockResolvedValue(ELIGIBLE_REGION);
    mockGetMyReservations.mockResolvedValue({
      reservations: [
        {
          id: 'res-1',
          station_id: VALID_STATION_ID,
          ship_type: 'TRADEDOCK_CONSTRUCTION',
          state: 'complete',
          created_at: new Date(Date.now() - 90 * 86400 * 1000).toISOString(),
          overall_progress_percent: 100,
        },
      ],
    });

    await act(async () => {
      root.render(<RegionTradeDockPanel regionId="region-1" isOwner={true} />);
    });
    await flush();

    expect(container.querySelector('.rtd-progress-card')?.textContent).toContain(
      'awaiting activation'
    );
  });
});
