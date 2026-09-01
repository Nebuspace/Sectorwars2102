import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GalacticCitizenAdminPanel from './GalacticCitizenAdminPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

const playerId = '11111111-1111-4111-8111-111111111111';

describe('GalacticCitizenAdminPanel (LEG-3617)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
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
});
