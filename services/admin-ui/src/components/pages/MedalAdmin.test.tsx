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

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

describe('MedalAdmin', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('loads catalog and renders Catalog tab rows', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return {
          data: {
            total: 1,
            items: [
              {
                id: 'bronze_cluster',
                name: 'Bronze Cluster',
                category: 'combat',
                tier: 'bronze',
                description: 'First blood',
              },
            ],
          },
        };
      }
      return { data: { players: [{ id: 'p1', username: 'Ace' }] } };
    });

    render(<MedalAdmin />);

    fireEvent.click(screen.getByRole('tab', { name: 'Catalog' }));

    await waitFor(() => {
      expect(screen.getByTestId('medal-catalog-table')).toBeTruthy();
    });
    expect(screen.getByText('Bronze Cluster')).toBeTruthy();
    expect(api.get).toHaveBeenCalledWith('/api/v1/medals/admin/catalog');
  });

  it('posts grant with player + medal', async () => {
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
    vi.mocked(api.post).mockResolvedValue({
      data: {
        success: true,
        changed: true,
        player_id: 'p1',
        medal_id: 'bronze_cluster',
        message: 'Medal granted',
      },
    });

    render(<MedalAdmin />);

    await waitFor(() => {
      expect(screen.getByLabelText('Select player')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Select player'), { target: { value: 'p1' } });
    fireEvent.change(screen.getByLabelText('Medal'), { target: { value: 'bronze_cluster' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grant medal' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/medals/admin/grant', {
        player_id: 'p1',
        medal_id: 'bronze_cluster',
        reason: null,
      });
    });
  });

  it('posts revoke with required reason', async () => {
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
    vi.mocked(api.post).mockResolvedValue({
      data: {
        success: true,
        changed: true,
        player_id: 'p1',
        medal_id: 'bronze_cluster',
        message: 'Medal revoked',
      },
    });

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
      expect(api.post).toHaveBeenCalledWith('/api/v1/medals/admin/revoke', {
        player_id: 'p1',
        medal_id: 'bronze_cluster',
        reason: 'Admin correction',
      });
    });
  });
});
