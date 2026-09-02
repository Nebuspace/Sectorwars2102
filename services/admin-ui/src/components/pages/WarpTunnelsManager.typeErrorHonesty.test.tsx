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
 * LEG-3418 Soft-ORDER — WarpTunnelsManager TypeError/Network Error honesty densify.
 * LEG-3928 Soft-ORDER — 403/429 HTTP honesty densify.
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

  it('surfaces 403 with warp tunnel scope copy when list GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Access denied|warp tunnel scopes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Access denied|warp tunnel scopes/i).textContent ?? '';
    expect(text).toMatch(/Access denied|warp tunnel scopes/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on warp tunnels GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

});
