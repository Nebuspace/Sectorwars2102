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

const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

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

/**
 * LEG-3666 Soft-ORDER — BountyAdminPanel TypeError/Network Error densify.
 * LEG-3870 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('BountyAdminPanel typeErrorHonesty densify (LEG-3666)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on bounty list load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<BountyAdminPanel />);
    fireEvent.change(screen.getByLabelText('Target player UUID'), { target: { value: 't1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load bounties/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on bounty list load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<BountyAdminPanel />);
    fireEvent.change(screen.getByLabelText('Target player UUID'), { target: { value: 't1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load bounties/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on force-cancel POST without leaking raw transport text', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    await confirmForceCancel();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Force-cancel failed/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on force-cancel POST without leaking transport text', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    await confirmForceCancel();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Force-cancel failed/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on collapse POST without leaking raw transport text', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Collapse failed/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on collapse POST without leaking transport text', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    fireEvent.click(screen.getByRole('button', { name: 'Collapse excess' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Collapse failed/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with PLAYERS_VIEW scope hint when bounties GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<BountyAdminPanel />);
    fireEvent.change(screen.getByLabelText('Target player UUID'), { target: { value: 't1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|PLAYERS_VIEW/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on bounties GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<BountyAdminPanel />);
    fireEvent.change(screen.getByLabelText('Target player UUID'), { target: { value: 't1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces force-cancel POST 403 with formatAdminApiError-friendly copy', async () => {
    await loadTargetWithBounty();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    await confirmForceCancel();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Access denied|ECONOMY_INTERVENE/i);
    expect(msg).not.toMatch(/\b403\b/);
    expect(msg).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(msg);
  });
});
