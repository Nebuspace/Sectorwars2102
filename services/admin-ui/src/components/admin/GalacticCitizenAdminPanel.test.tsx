import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import GalacticCitizenAdminPanel from './GalacticCitizenAdminPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const toastSuccess = vi.fn();
const toastInfo = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn(async () => true);

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
    warning: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

describe('GalacticCitizenAdminPanel (LEG-273)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastInfo.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  const axiosError = (status: number, detail?: string) =>
    Object.assign(new Error(`HTTP ${status}`), {
      response: { status, data: detail ? { detail } : {} },
    });

  it('loads GC status from subscriptions overview and grants with required reason', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        galactic_citizens: [{ player_id: 'p1', username: 'Hero' }],
      },
    });

    render(<GalacticCitizenAdminPanel playerId="p1" playerName="Hero" />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/subscriptions');
    });
    expect(await screen.findByText(/Active Galactic Citizen/)).toBeTruthy();

    const grantBtn = screen.getByRole('button', { name: 'Grant GC' });
    expect(grantBtn).toBeDisabled();

    fireEvent.change(screen.getByTestId('gc-mutation-reason'), {
      target: { value: 'Support comp ticket #99' },
    });
    expect(grantBtn).not.toBeDisabled();

    vi.mocked(api.post).mockResolvedValue({
      data: {
        player_id: 'p1',
        is_galactic_citizen: true,
        subscription_tier: 'galactic_citizen',
        changed: false,
        idempotent: true,
        message: 'Galactic citizenship already active',
      },
    });

    fireEvent.click(grantBtn);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/players/p1/galactic-citizen/grant',
        { reason: 'Support comp ticket #99' },
      );
    });
    expect(toastInfo).toHaveBeenCalledWith('Galactic citizenship already active');
  });

  it('surfaces 403 scope denial on grant POST', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { galactic_citizens: [] } });
    render(<GalacticCitizenAdminPanel playerId="p2" />);

    await waitFor(() => expect(api.get).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId('gc-mutation-reason'), {
      target: { value: 'Clawback fraud case' },
    });
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.subscriptions.modify'),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Revoke GC' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Missing scope admin.subscriptions.modify');
    });
  });

  it('shows view-scope warning when subscriptions overview is forbidden without detail', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<GalacticCitizenAdminPanel playerId="p3" />);

    expect(
      await screen.findByRole('alert'),
    ).toHaveTextContent(/admin\.subscriptions\.view/i);
  });

  it('does not POST when confirm is cancelled', async () => {
    confirmMock.mockResolvedValue(false);
    vi.mocked(api.get).mockResolvedValue({ data: { galactic_citizens: [] } });

    render(<GalacticCitizenAdminPanel playerId="p4" />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId('gc-mutation-reason'), {
      target: { value: 'reason' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Grant GC' }));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(api.post).not.toHaveBeenCalled();
  });
});
