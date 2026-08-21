import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import TradeDockAdmin from './TradeDockAdmin';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

describe('TradeDockAdmin', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('loads TradeDock list, slip pools, active reservations, opens detail', async () => {
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
  });
});
