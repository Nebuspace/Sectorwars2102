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
  useConfirm: () => vi.fn(async () => true),
}));

describe('BountyAdminPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
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

  function fillFactionBountyForm() {
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
  }

  it('posts faction bounty for an NPC', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, amount: 2000 },
    });

    fillFactionBountyForm();
    fireEvent.click(screen.getByRole('button', { name: 'Place faction bounty' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/npcs/npc-1/faction-bounty', {
        faction_type: 'Federation',
        amount: 2000,
        reason: 'Pirate captain',
      });
    });
  });

  it('surfaces formatAdminApiError on faction bounty POST 403 (LEG-2760)', async () => {
    fillFactionBountyForm();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

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

  it('surfaces rate-limit copy on faction bounty POST 429 (LEG-2760)', async () => {
    fillFactionBountyForm();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

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

    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));

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

    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));

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
