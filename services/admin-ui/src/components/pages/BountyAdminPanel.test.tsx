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

const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

describe('BountyAdminPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it('loads player bounties and force-cancels an entry above ₡1,000 after inline confirm', async () => {
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

    const forceCancelBtn = await screen.findByRole('button', { name: 'Force-cancel' });
    fireEvent.click(forceCancelBtn);
    expect(api.post).not.toHaveBeenCalled();

    const confirmBtn = await screen.findByRole('button', { name: /Confirm\? · ₡5,000 refund/ });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b1/force-cancel'
      );
    });
  });

  it('force-cancels at or below ₡1,000 in one click (ADR-0093)', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        success: true,
        target_id: 't1',
        target_name: 'Wanted',
        player_bounties: [
          {
            id: 'b-low',
            placed_by: 'p2',
            placed_by_name: 'Placer',
            amount: 1000,
            type: 'player',
          },
        ],
        system_bounties: [],
        total_value: 1000,
      },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, refund: 1000, refunded: true },
    });

    render(<BountyAdminPanel />);

    fireEvent.change(screen.getByLabelText('Target player UUID'), {
      target: { value: 't1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    const forceCancelBtn = await screen.findByRole('button', { name: 'Force-cancel' });
    fireEvent.click(forceCancelBtn);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b-low/force-cancel'
      );
    });
    expect(screen.queryByRole('button', { name: /Confirm\?/ })).toBeNull();
  });

  it('posts faction bounty for an NPC after inline confirm when amount > ₡1,000', async () => {
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
    expect(api.post).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole('button', { name: /Confirm\? · ₡2,000/ }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/npcs/npc-1/faction-bounty', {
        faction_type: 'Federation',
        amount: 2000,
        reason: 'Pirate captain',
      });
    });
  });

  it('posts faction bounty at exactly ₡1,000 in one click', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, amount: 1000 },
    });

    render(<BountyAdminPanel />);

    fireEvent.change(screen.getByLabelText('NPC UUID'), {
      target: { value: 'npc-1' },
    });
    fireEvent.change(screen.getByLabelText('Reason'), {
      target: { value: 'Minimum stake' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Place faction bounty' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/npcs/npc-1/faction-bounty', {
        faction_type: 'Federation',
        amount: 1000,
        reason: 'Minimum stake',
      });
    });
  });

  it('posts collapse for a loaded target in one click (no credit movement)', async () => {
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

  async function loadTargetWithBounty() {
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
  }

  async function confirmForceCancel() {
    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));
    fireEvent.click(await screen.findByRole('button', { name: /Confirm\? · ₡5,000 refund/ }));
  }

  it('surfaces formatAdminApiError on collapse POST 403 (LEG-2650)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/collapse');
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/ECONOMY_INTERVENE|Access denied/i);
    expect(toastError).not.toHaveBeenCalledWith('Collapse failed');
  });

  it('surfaces rate-limit copy on collapse POST 429 (LEG-2650)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/collapse');
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Collapse failed');
  });

  it('surfaces formatAdminApiError on force-cancel POST 403 (LEG-2651)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    await confirmForceCancel();

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b1/force-cancel',
      );
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/ECONOMY_INTERVENE|Access denied/i);
    expect(toastError).not.toHaveBeenCalledWith('Force-cancel failed');
  });

  it('surfaces rate-limit copy on force-cancel POST 429 (LEG-2651)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await confirmForceCancel();

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b1/force-cancel',
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Force-cancel failed');
  });

  it('surfaces formatAdminApiError on faction-bounty POST 403 (LEG-2880)', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

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
    expect(String(toastError.mock.calls[0][0])).toMatch(/ECONOMY_INTERVENE|Access denied/i);
    expect(toastError).not.toHaveBeenCalledWith('Faction bounty failed');
  });

  it('surfaces rate-limit copy on faction-bounty POST 429 (LEG-2880)', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

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
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Faction bounty failed');
  });

  const axiosError = (status: number) =>
    Object.assign(new Error(`HTTP ${status}`), { response: { status } });

  it('reports load 403 as PLAYERS_VIEW scope problem, not bare Failed to load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));
    render(<BountyAdminPanel />);
    fireEvent.change(screen.getByLabelText('Target player UUID'), { target: { value: 't1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(alert).not.toMatch(/^Failed to load bounties$/);
  });

  it('reports load 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));
    render(<BountyAdminPanel />);
    fireEvent.change(screen.getByLabelText('Target player UUID'), { target: { value: 't1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/Failed to load bounties/);
  });
});
