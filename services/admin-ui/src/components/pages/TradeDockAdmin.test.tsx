import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import TradeDockAdmin from './TradeDockAdmin';
import { api } from '../../utils/auth';

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn(async () => true);

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

function mockTradeDockGets() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/admin/construction/tradedocks')) {
      return {
        data: {
          tradedocks: [
            {
              station_id: 'st1',
              name: 'TradeDock Prime',
              tradedock_tier: 'A',
              sector_id: 50,
            },
          ],
        },
      };
    }
    if (url.includes('/admin/construction/tradedocks/st1')) {
      return {
        data: {
          station_id: 'st1',
          station_name: 'TradeDock Prime',
          tradedock_tier: 'A',
          slips: {
            standard: { capacity: 10, in_use: 1 },
            specialized: { capacity: 2, in_use: 0 },
          },
          queue_length: 1,
          queue: [
            {
              position: 1,
              reservation_id: 'r2',
              player_id: 'p2',
              ship_type: 'SCOUT',
              priority_bumps_count: 0,
            },
          ],
          reservations: [
            {
              id: 'r1',
              ship_type: 'WARP_JUMPER',
              state: 'frame_assembly',
              uses_specialized_slip: false,
              overall_progress_percent: 12.5,
            },
          ],
          reservation_count_active: 2,
          reservation_count_total: 3,
        },
      };
    }
    if (url.includes('/admin/construction/reservations/r1')) {
      return {
        data: {
          id: 'r1',
          station_id: 'st1',
          ship_type: 'WARP_JUMPER',
          state: 'frame_assembly',
          total_cost: 1000000,
          deposit_paid: 100000,
          credits_paid: 100000,
          uses_specialized_slip: false,
          overall_progress_percent: 12.5,
        },
      };
    }
    return { data: {} };
  });
}

function httpErr(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });
}

function mockTradeDockListOnly() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/admin/construction/tradedocks')) {
      return {
        data: {
          tradedocks: [
            {
              station_id: 'st1',
              name: 'TradeDock Prime',
              tradedock_tier: 'A',
              sector_id: 50,
            },
          ],
        },
      };
    }
    return { data: {} };
  });
}

async function openReservationDetail() {
  mockTradeDockGets();
  render(<TradeDockAdmin />);

  await waitFor(() => {
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks');
  });

  fireEvent.click(await screen.findByLabelText('Open reservation r1'));

  await waitFor(() => {
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/reservations/r1');
  });
  expect(await screen.findByLabelText('Force-cancel reservation r1')).toBeTruthy();
}

describe('TradeDockAdmin', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('loads TradeDock list, slip pools, active reservations, opens detail', async () => {
    mockTradeDockGets();
    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks');
    });

    expect(await screen.findByLabelText('TradeDock station')).toBeTruthy();
    expect(await screen.findByLabelText('Standard slips 1 / 10')).toBeTruthy();
    expect(screen.getByLabelText('Specialized slips 0 / 2')).toBeTruthy();
    expect(screen.getByLabelText('Total slips 1 / 12')).toBeTruthy();
    expect(screen.getByText('WARP_JUMPER')).toBeTruthy();
    expect(screen.getByText('p2')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Open reservation r1'));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/reservations/r1');
    });
    expect(await screen.findByText(/1,000,000/)).toBeTruthy();
    expect(screen.getByLabelText('Force-cancel reservation r1')).toBeTruthy();
  });

  it('force-cancel posts tip path and toasts refund credits after inline confirm (ADR-0093)', async () => {
    await openReservationDetail();
    vi.mocked(api.post).mockResolvedValue({
      data: {
        message: 'Reservation force-cancelled — 50,000 credits refunded',
        refund: 50000,
      },
    });

    fireEvent.click(screen.getByLabelText('Force-cancel reservation r1'));
    expect(api.post).not.toHaveBeenCalled();

    fireEvent.click(
      await screen.findByRole('button', { name: /Confirm\? · ₡100,000 refund/ })
    );

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/construction/reservations/r1/force-cancel'
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith(
      'Reservation force-cancelled — 50,000 credits refunded'
    );
  });

  it('force-cancels at or below ₡1,000 refund in one click (ADR-0093)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        return {
          data: {
            station_id: 'st1',
            station_name: 'TradeDock Prime',
            tradedock_tier: 'A',
            slips: {
              standard: { capacity: 10, in_use: 1 },
              specialized: { capacity: 2, in_use: 0 },
            },
            queue_length: 0,
            queue: [],
            reservations: [
              {
                id: 'r-low',
                ship_type: 'SCOUT',
                state: 'queued',
                deposit_paid: 1000,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/reservations/r-low')) {
        return {
          data: {
            id: 'r-low',
            ship_type: 'SCOUT',
            state: 'queued',
            deposit_paid: 1000,
          },
        };
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks');
    });

    fireEvent.click(await screen.findByLabelText('Open reservation r-low'));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/reservations/r-low');
    });

    vi.mocked(api.post).mockResolvedValue({
      data: { message: 'Reservation force-cancelled — 1,000 credits refunded', refund: 1000 },
    });

    fireEvent.click(screen.getByLabelText('Force-cancel reservation r-low'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/construction/reservations/r-low/force-cancel'
      );
    });
    expect(screen.queryByRole('button', { name: /Confirm\?/ })).toBeNull();
  });

  it('force-cancel 403 surfaces formatAdminApiError scope helper copy', async () => {
    await openReservationDetail();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });

    fireEvent.click(screen.getByLabelText('Force-cancel reservation r1'));
    fireEvent.click(
      await screen.findByRole('button', { name: /Confirm\? · ₡100,000 refund/ })
    );

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/admin/construction/reservations/r1/force-cancel'
    );
  });

  it('force-cancel 429 surfaces admin rate-limit helper copy', async () => {
    await openReservationDetail();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 429, data: {} },
    });

    fireEvent.click(screen.getByLabelText('Force-cancel reservation r1'));
    fireEvent.click(
      await screen.findByRole('button', { name: /Confirm\? · ₡100,000 refund/ })
    );

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        expect.stringMatching(/rate limit/i)
      );
    });
  });

  it('surfaces honest fallback on force-cancel TypeError/network collapse (LEG-2974)', async () => {
    await openReservationDetail();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    fireEvent.click(screen.getByLabelText('Force-cancel reservation r1'));
    fireEvent.click(
      await screen.findByRole('button', { name: /Confirm\? · ₡100,000 refund/ })
    );

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/construction/reservations/r1/force-cancel'
      );
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Force-cancel failed/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error to TradeDock list load fallback (LEG-3358)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load TradeDocks/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error to force-cancel fallback (LEG-3358)', async () => {
    await openReservationDetail();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    fireEvent.click(screen.getByLabelText('Force-cancel reservation r1'));
    fireEvent.click(
      await screen.findByRole('button', { name: /Confirm\? · ₡100,000 refund/ })
    );

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/construction/reservations/r1/force-cancel'
      );
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/Force-cancel failed/i));
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('list load 403 surfaces formatAdminApiError scope helper (not generic Failed to load)', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    render(<TradeDockAdmin />);

    expect(
      await screen.findByText(/Access denied|PLAYERS_VIEW/i)
    ).toBeTruthy();
    expect(screen.queryByText('Failed to load TradeDocks')).toBeNull();
  });

  it('list load 429 surfaces admin rate-limit helper (not generic Failed to load)', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    render(<TradeDockAdmin />);

    expect(await screen.findByText(/rate limit/i)).toBeTruthy();
    expect(screen.queryByText('Failed to load TradeDocks')).toBeNull();
  });

  it('overview GET 403 surfaces scope helper on detailError (LEG-2890)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        throw { response: { status: 403, data: {} } };
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks/st1');
    });

    expect(await screen.findByText(/PLAYERS_VIEW|Access denied/i)).toBeTruthy();
    expect(screen.queryByText('Failed to load TradeDock overview')).toBeNull();
  });

  it('overview GET 429 surfaces rate-limit helper on detailError (LEG-2890)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        throw { response: { status: 429, data: {} } };
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks/st1');
    });

    expect(await screen.findByText(/rate limit/i)).toBeTruthy();
    expect(screen.queryByText('Failed to load TradeDock overview')).toBeNull();
  });

  it('reservation detail GET 403 surfaces scope helper via toast (LEG-2890)', async () => {
    mockTradeDockGets();
    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks');
    });

    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/construction/reservations/r1')) {
        throw { response: { status: 403, data: {} } };
      }
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        return {
          data: {
            station_id: 'st1',
            station_name: 'TradeDock Prime',
            tradedock_tier: 'A',
            slips: {
              standard: { capacity: 10, in_use: 1 },
              specialized: { capacity: 2, in_use: 0 },
            },
            queue_length: 0,
            queue: [],
            reservations: [
              {
                id: 'r1',
                ship_type: 'WARP_JUMPER',
                state: 'frame_assembly',
                uses_specialized_slip: false,
                overall_progress_percent: 12.5,
              },
            ],
          },
        };
      }
      return { data: {} };
    });

    fireEvent.click(await screen.findByLabelText('Open reservation r1'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(String(toastError.mock.calls[0][0])).not.toMatch(/^Failed to load reservation detail$/);
  });

  it('reservation detail GET 429 surfaces rate-limit helper via toast (LEG-2890)', async () => {
    mockTradeDockGets();
    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/tradedocks');
    });

    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/construction/reservations/r1')) {
        throw { response: { status: 429, data: {} } };
      }
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        return {
          data: {
            station_id: 'st1',
            station_name: 'TradeDock Prime',
            tradedock_tier: 'A',
            slips: {
              standard: { capacity: 10, in_use: 1 },
              specialized: { capacity: 2, in_use: 0 },
            },
            queue_length: 0,
            queue: [],
            reservations: [
              {
                id: 'r1',
                ship_type: 'WARP_JUMPER',
                state: 'frame_assembly',
                uses_specialized_slip: false,
                overall_progress_percent: 12.5,
              },
            ],
          },
        };
      }
      return { data: {} };
    });

    fireEvent.click(await screen.findByLabelText('Open reservation r1'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
    expect(String(toastError.mock.calls[0][0])).not.toMatch(/^Failed to load reservation detail$/);
  });
});

describe('TradeDockAdmin overview + reservation detail GET errors (LEG-2683)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('overview GET 403 surfaces detailError scope copy (not generic Failed to load)', async () => {
    mockTradeDockListOnly();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        throw httpErr(403);
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied|PLAYERS_VIEW/i);
    });
    expect(screen.getByRole('alert').textContent).not.toMatch(
      /^Failed to load TradeDock overview$/i
    );
  });

  it('overview GET 429 surfaces detailError rate-limit copy (not generic Failed to load)', async () => {
    mockTradeDockListOnly();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        throw httpErr(429);
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.getByRole('alert').textContent).not.toMatch(
      /^Failed to load TradeDock overview$/i
    );
  });

  it('reservation detail GET 403 toast uses formatAdminApiError scope copy', async () => {
    mockTradeDockGets();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        return {
          data: {
            station_id: 'st1',
            station_name: 'TradeDock Prime',
            tradedock_tier: 'A',
            slips: {
              standard: { capacity: 10, in_use: 1 },
              specialized: { capacity: 2, in_use: 0 },
            },
            queue_length: 0,
            queue: [],
            reservations: [
              {
                id: 'r1',
                ship_type: 'WARP_JUMPER',
                state: 'frame_assembly',
                uses_specialized_slip: false,
                overall_progress_percent: 12.5,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/reservations/r1')) {
        throw httpErr(403);
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    fireEvent.click(await screen.findByLabelText('Open reservation r1'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(String(toastError.mock.calls[0][0])).not.toMatch(
      /^Failed to load reservation detail$/i
    );
  });

  it('reservation detail GET 429 toast uses admin rate-limit helper copy', async () => {
    mockTradeDockGets();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            tradedocks: [
              {
                station_id: 'st1',
                name: 'TradeDock Prime',
                tradedock_tier: 'A',
                sector_id: 50,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        return {
          data: {
            station_id: 'st1',
            station_name: 'TradeDock Prime',
            tradedock_tier: 'A',
            slips: {
              standard: { capacity: 10, in_use: 1 },
              specialized: { capacity: 2, in_use: 0 },
            },
            queue_length: 0,
            queue: [],
            reservations: [
              {
                id: 'r1',
                ship_type: 'WARP_JUMPER',
                state: 'frame_assembly',
                uses_specialized_slip: false,
                overall_progress_percent: 12.5,
              },
            ],
          },
        };
      }
      if (url.includes('/admin/construction/reservations/r1')) {
        throw httpErr(429);
      }
      return { data: {} };
    });

    render(<TradeDockAdmin />);

    fireEvent.click(await screen.findByLabelText('Open reservation r1'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });
});
