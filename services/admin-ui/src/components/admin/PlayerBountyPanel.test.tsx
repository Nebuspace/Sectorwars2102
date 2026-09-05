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

describe('PlayerBountyPanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  const axiosError = (status: number) =>
    Object.assign(new Error(`HTTP ${status}`), { response: { status } });

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

    render(<PlayerBountyPanel targetId="t1" targetName="Wanted" />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties');
    });
    expect(await screen.findByRole('button', { name: 'Force-cancel' })).toBeTruthy();
  }

  async function confirmForceCancel() {
    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));
    fireEvent.click(await screen.findByRole('button', { name: /Confirm\? · ₡5,000 refund/ }));
  }

  it('auto-loads bounties for targetId and force-cancels an entry above ₡1,000 after inline confirm', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, refund: 5000, refunded: true },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Force-cancel' }));
    expect(api.post).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole('button', { name: /Confirm\? · ₡5,000 refund/ }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/b1/force-cancel');
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

    render(<PlayerBountyPanel targetId="t1" targetName="Wanted" />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties');
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Force-cancel' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b-low/force-cancel'
      );
    });
    expect(screen.queryByRole('button', { name: /Confirm\?/ })).toBeNull();
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

  it('shows PLAYERS_VIEW denial on load 403', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<PlayerBountyPanel targetId="t1" />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent ?? '').toMatch(/PLAYERS_VIEW|Access denied/i);
    });
  });

  it('surfaces honest fallback on load TypeError/network collapse (LEG-3018)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PlayerBountyPanel targetId="t1" />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load bounties/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on initial bounty load to honest fallback (LEG-3517)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<PlayerBountyPanel targetId="t1" />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load bounties/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toMatch(/Network Error/i);
  });

  it('shows admin rate-limit on load 429', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<PlayerBountyPanel targetId="t1" />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
    });
  });

  it('surfaces formatAdminApiError on collapse POST 403 (LEG-2672)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/collapse');
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/ECONOMY_INTERVENE|Access denied/i);
    expect(toastError).not.toHaveBeenCalledWith('Collapse failed');
  });

  it('surfaces rate-limit copy on collapse POST 429 (LEG-2672)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/t1/bounties/collapse');
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Collapse failed');
  });

  it('surfaces formatAdminApiError on force-cancel POST 403 (LEG-2672)', async () => {
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

  it('surfaces rate-limit copy on force-cancel POST 429 (LEG-2672)', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await confirmForceCancel();

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/t1/bounties/b1/force-cancel',
      );
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Force-cancel failed');
  });
});
