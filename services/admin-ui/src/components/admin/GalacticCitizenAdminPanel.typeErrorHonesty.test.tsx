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


const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3630 Soft-ORDER — GalacticCitizenAdminPanel TypeError/Network Error densify.
 * LEG-3881 Soft-ORDER — 429 HTTP honesty densify.
 */
describe('GalacticCitizenAdminPanel typeErrorHonesty densify (LEG-3630)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
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
      target: { value: 'Transport probe' },
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
      target: { value: 'Transport probe' },
    });
    await user.click(screen.getByRole('button', { name: /^Revoke GC$/i }));
  }

  it('collapses axios Network Error on grant without leaking raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));
    await submitGrant();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to grant Galactic Citizenship/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on revoke without leaking transport text', async () => {
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

  it('surfaces 403 with admin.subscriptions.modify scope hint', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    await submitRevoke();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/admin\.subscriptions\.modify/i);
  });

  it('surfaces 429 as admin rate-limit copy on revoke POST', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
    await submitRevoke();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });

});
