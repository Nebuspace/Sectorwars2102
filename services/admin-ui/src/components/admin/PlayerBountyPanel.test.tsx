import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlayerBountyPanel from './PlayerBountyPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

describe('PlayerBountyPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('auto-loads bounties for targetId and force-cancels an entry', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        success: true,
        target_id: 't1',
        target_name: 'Wanted',
        player_bounties: [
          {
            id: 'b1',
            placed_by: 'p2',
            placed_by_name: 'Placer',
            amount: 5000,
            type: 'player',
          },
        ],
        system_bounties: [],
        total_value: 5000,
      },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, refund: 5000, refunded: true },
    });

    render(<PlayerBountyPanel targetId="t1" targetName="Wanted" />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/b1/force-cancel');
    });
  });

  it('calls collapse endpoint', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        success: true,
        target_id: 't1',
        player_bounties: [],
        system_bounties: [],
        total_value: 0,
      },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { collapsed: 2, entry_count: 48 },
    });

    render(<PlayerBountyPanel targetId="t1" />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/collapse');
    });
  });

  it('shows load error when GET bounties fails', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { data: { detail: 'Not found' } },
    });

    render(<PlayerBountyPanel targetId="missing-player" />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Not found');
    });
  });
});
