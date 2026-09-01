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

/**
 * LEG-3623 Soft-ORDER — AriaPlayerSecurityOpsPanel TypeError/Network Error densify.
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
});
