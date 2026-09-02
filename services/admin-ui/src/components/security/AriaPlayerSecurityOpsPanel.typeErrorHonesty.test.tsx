import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AriaPlayerSecurityOpsPanel } from './AriaPlayerSecurityOpsPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
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
 * LEG-3623 Soft-ORDER — AriaPlayerSecurityOpsPanel TypeError/Network Error densify.
 * LEG-3944 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError.
 */
describe('AriaPlayerSecurityOpsPanel typeErrorHonesty densify (LEG-3623)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on risk/status load', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load player (risk assessment|security status)/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on risk/status load', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load player (risk assessment|security status)/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with admin.aria.audit scope copy on risk/status load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied/i);
    expect(alert).toMatch(/admin\.aria\.audit/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 rate-limit copy on risk/status load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
