import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import BountyAdminPanel from './BountyAdminPanel';
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

describe('BountyAdminPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('loads player bounties and force-cancels an entry', async () => {
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

    render(<BountyAdminPanel />);

    const targetInput = screen.getByLabelText('Target player UUID');
    fireEvent.change(targetInput, { target: { value: 't1' } });
    await waitFor(() => {
      expect((targetInput as HTMLInputElement).value).toBe('t1');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties');
    });
    expect(await screen.findByRole('button', { name: 'Force-cancel' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b1/force-cancel'
      );
    });
  });

  it('posts faction bounty for an NPC', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, amount: 2000 },
    });

    render(<BountyAdminPanel />);

    fireEvent.change(screen.getByLabelText('NPC UUID'), {
      target: { value: 'npc-1' },
    });
    fireEvent.change(screen.getByLabelText('Amount (≥ 1000)'), {
      target: { value: '2000' },
    });
    fireEvent.change(screen.getByLabelText('Reason'), {
      target: { value: 'Pirate captain' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Place faction bounty' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/npcs/npc-1/faction-bounty', {
        faction_type: 'Federation',
        amount: 2000,
        reason: 'Pirate captain',
      });
    });
  });

  it('posts collapse for a loaded target', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        success: true,
        target_id: 't1',
        target_name: 'Wanted',
        player_bounties: [{ id: 'b1', placed_by_name: 'Placer', amount: 5000 }],
        system_bounties: [],
        total_value: 5000,
      },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { collapsed: 3, entry_count: 47, message: 'ok' },
    });

    render(<BountyAdminPanel />);

    const targetInput = screen.getByLabelText('Target player UUID');
    fireEvent.change(targetInput, { target: { value: 't1' } });
    await waitFor(() => {
      expect((targetInput as HTMLInputElement).value).toBe('t1');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    expect(await screen.findByRole('button', { name: 'Force-cancel' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/collapse');
    });
  });
});
