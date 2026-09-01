import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import EconomyLeversPanel from './EconomyLeversPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

/**
 * LEG-3657 Soft-ORDER — EconomyLeversPanel TypeError/Network Error densify.
 */
describe('EconomyLeversPanel typeErrorHonesty densify (LEG-3657)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on initial load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Gameserver unreachable|network error loading economy levers/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on initial load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Gameserver unreachable|network error loading economy levers/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
