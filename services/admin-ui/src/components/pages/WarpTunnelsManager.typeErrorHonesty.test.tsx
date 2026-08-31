import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import WarpTunnelsManager from './WarpTunnelsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
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
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

/**
 * LEG-3418 Soft-ORDER — WarpTunnelsManager TypeError/Network Error honesty densify.
 * formatAdminApiError collapses transport failures to warp-tunnels fallback.
 */
describe('WarpTunnelsManager typeErrorHonesty densify (LEG-3418)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error to warp-tunnels fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch warp tunnels/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to fetch warp tunnels/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch to warp-tunnels fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch warp tunnels/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to fetch warp tunnels/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toBe('Failed to fetch');
  });
});
