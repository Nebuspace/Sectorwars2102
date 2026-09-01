import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

const playerId = '11111111-1111-4111-8111-111111111111';

describe('GalacticCitizenAdminPanel embedded (LEG-273)', () => {
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

describe('GalacticCitizenAdminPanel standalone (LEG-3617 / LEG-3632)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('posts grant with reason and shows API message + is_galactic_citizen', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValue({
      data: {
        player_id: playerId,
        is_galactic_citizen: true,
        subscription_tier: 'galactic_citizen',
        changed: true,
        idempotent: false,
        message: 'Galactic citizenship granted',
      },
    });

    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Support comp — ticket 42' },
    });
    await user.click(screen.getByRole('button', { name: /^Grant GC$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/api/v1/admin/players/${encodeURIComponent(playerId)}/galactic-citizen/grant`,
        { reason: 'Support comp — ticket 42' },
      );
    });
    expect(screen.getByRole('status').textContent).toMatch(/Galactic citizenship granted/);
    expect(screen.getByRole('status').textContent).toMatch(/is_galactic_citizen=true/);
  });

  it('surfaces idempotent grant messaging from API response', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValue({
      data: {
        player_id: playerId,
        is_galactic_citizen: true,
        subscription_tier: 'galactic_citizen',
        changed: false,
        idempotent: true,
        message: 'Galactic citizenship already active',
      },
    });

    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Repeat grant probe' },
    });
    await user.click(screen.getByRole('button', { name: /^Grant GC$/i }));

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toMatch(/already active/i);
    });
    expect(screen.getByRole('status').textContent).toMatch(/idempotent/i);
  });

  it('surfaces 403 with SUBSCRIPTIONS_MODIFY scope hint', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });

    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Scope probe' },
    });
    await user.click(screen.getByRole('button', { name: /^Revoke GC$/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/admin\.subscriptions\.modify/i);
  });

  it('collapses axios Network Error without leaking raw transport text', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Transport probe' },
    });
    await user.click(screen.getByRole('button', { name: /^Grant GC$/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to grant Galactic Citizenship/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on revoke to operator fallback (LEG-3630)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Clawback fraud case' },
    });
    await user.click(screen.getByRole('button', { name: /^Revoke GC$/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to revoke Galactic Citizenship/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });
});
