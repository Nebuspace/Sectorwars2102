import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import MedalAdmin from './MedalAdmin';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

const mockToastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: mockToastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

/**
 * LEG-3416 Soft-ORDER — MedalAdmin TypeError/Network Error honesty densify.
 * formatAdminApiError on grant/revoke/collection paths collapses transport to fallbacks.
 */
describe('MedalAdmin typeErrorHonesty densify (LEG-3416)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockToastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on catalog load to honest fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        throw new Error('Network Error');
      }
      return { data: { players: [] } };
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Catalog' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load medal catalog/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on catalog load to honest fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: { players: [] } };
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Catalog' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load medal catalog/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError on revoke mutation toast to operator fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return {
          data: {
            total: 1,
            items: [{ id: 'bronze_cluster', name: 'Bronze Cluster', category: 'combat' }],
          },
        };
      }
      return { data: { players: [{ id: 'p1', username: 'Ace' }] } };
    });
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Revoke' }));

    await waitFor(() => {
      expect(screen.getByLabelText('Select player')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Select player'), { target: { value: 'p1' } });
    fireEvent.change(screen.getByLabelText('Medal'), { target: { value: 'bronze_cluster' } });
    fireEvent.change(screen.getByLabelText(/Reason/), {
      target: { value: 'Admin correction' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Revoke medal' }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });
    const msg = String(mockToastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Revoke failed/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
    expect(msg).not.toContain('Network Error');
  });
});
