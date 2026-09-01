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

const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

const playerId = '11111111-1111-4111-8111-111111111111';

/**
 * LEG-3630 Soft-ORDER — GalacticCitizenAdminPanel TypeError/Network Error honesty densify.
 */
describe('GalacticCitizenAdminPanel typeErrorHonesty densify (LEG-3630)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  async function submitGrant() {
    const user = userEvent.setup();
    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Support comp ticket #99' },
    });
    await user.click(screen.getByRole('button', { name: /^Grant GC$/i }));
  }

  async function submitRevoke() {
    const user = userEvent.setup();
    render(<GalacticCitizenAdminPanel />);
    fireEvent.change(screen.getByLabelText(/Player UUID/i), {
      target: { value: playerId },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: 'Clawback fraud case' },
    });
    await user.click(screen.getByRole('button', { name: /^Revoke GC$/i }));
  }

  it('collapses axios Network Error on grant to operator fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));
    await submitGrant();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to grant Galactic Citizenship/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on revoke to operator fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));
    await submitRevoke();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to revoke Galactic Citizenship/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 scope hint for admin.subscriptions.modify when detail absent', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    await submitGrant();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/admin\.subscriptions\.modify/i);
  });
});
