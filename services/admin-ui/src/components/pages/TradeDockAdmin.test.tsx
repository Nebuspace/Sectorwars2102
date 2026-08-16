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

  it('loads TradeDock list, renders 12-slip grid, opens reservation detail', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/admin/construction/tradedocks')) {
        return {
          data: {
            items: [{ id: 'st1', name: 'TradeDock Prime', tradedock_tier: 'A', queue_depth: 1 }],
          },
        };
      }
      if (url.includes('/admin/construction/tradedocks/st1')) {
        return {
          data: {
            station: { id: 'st1', name: 'TradeDock Prime', tradedock_tier: 'A' },
            slips: [
              {
                index: 1,
                reservation: {
                  id: 'r1',
                  player_id: 'p1',
                  player_nickname: 'Builder',
                  ship_type: 'WARP_JUMPER',
                  state: 'frame_assembly',
                },
              },
            ],
            queue: [
              {
                id: 'r2',
                player_id: 'p2',
                player_nickname: 'Waiter',
                ship_type: 'SCOUT',
                state: 'queued',
                queue_position: 1,
                priority_bumps_count: 0,
              },
            ],
            queue_depth: 1,
          },
        };
      }
      if (url.includes('/admin/construction/reservations/r1')) {
        return {
          data: {
            id: 'r1',
            player_id: 'p1',
            player_nickname: 'Builder',
            ship_type: 'WARP_JUMPER',
            state: 'frame_assembly',
            station_id: 'st1',
            total_cost: 1000000,
            deposit_paid: 100000,
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
    expect(await screen.findByLabelText('Slip 1: WARP_JUMPER (frame_assembly)')).toBeTruthy();
    expect(screen.getByLabelText('Slip 12: empty')).toBeTruthy();
    expect(screen.getByText('Waiter')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Slip 1: WARP_JUMPER (frame_assembly)'));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/construction/reservations/r1');
    });
    expect(await screen.findByText(/1,000,000/)).toBeTruthy();
  });
});
