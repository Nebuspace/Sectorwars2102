import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import StationsManager from './StationsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => false),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

/**
 * LEG-3485 Soft-ORDER — StationsManager TypeError/Network Error honesty densify.
 */
describe('StationsManager typeErrorHonesty densify (LEG-3485)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load to unexpected-error fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<StationsManager />);

    await waitFor(() => {
      expect(screen.getByText(/An unexpected error occurred/i)).toBeTruthy();
    });
    const text = screen.getByText(/An unexpected error occurred/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on load to unexpected-error fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<StationsManager />);

    await waitFor(() => {
      expect(screen.getByText(/An unexpected error occurred/i)).toBeTruthy();
    });
    const text = screen.getByText(/An unexpected error occurred/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});
