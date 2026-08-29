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

describe('MedalAdmin', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockToastError.mockReset();
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

  it('surfaces PLAYERS_ADJUST_REP scope denial on revoke instead of generic failure (LEG-2670)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.players.adjust_rep (PLAYERS_ADJUST_REP)' },
        },
      }),
    );

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
    expect(mockToastError.mock.calls[0][0]).toMatch(/PLAYERS_ADJUST_REP|adjust_rep/i);
    expect(mockToastError.mock.calls[0][0]).not.toMatch(/^Revoke failed$/);
  });

  it('surfaces admin rate-limit copy on revoke 429 (LEG-2670)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429, data: { detail: 'Too Many Requests' } },
      }),
    );

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
    expect(mockToastError.mock.calls[0][0]).toMatch(/rate limit/i);
    expect(mockToastError.mock.calls[0][0]).not.toMatch(/^Revoke failed$/);
  });

  it('bulk dry-run then commit happy path', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return {
          data: {
            total: 1,
            items: [{ id: 'bronze_cluster', name: 'Bronze Cluster', category: 'combat' }],
          },
        };
      }
      return { data: { players: [] } };
    });
    vi.mocked(api.post).mockImplementation(async (_url: string, body: unknown) => {
      const payload = body as { dry_run?: boolean };
      if (payload.dry_run) {
        return {
          data: {
            dry_run: true,
            medal_id: 'bronze_cluster',
            valid_count: 2,
            invalid_count: 0,
            already_held_count: 0,
            grantable_count: 2,
            granted_count: 0,
            invalid_samples: [],
            grant_batch_id: null,
            toast_suppressed: false,
          },
        };
      }
      return {
        data: {
          dry_run: false,
          medal_id: 'bronze_cluster',
          valid_count: 2,
          invalid_count: 0,
          already_held_count: 0,
          grantable_count: 2,
          granted_count: 2,
          invalid_samples: [],
          grant_batch_id: 'batch-111',
          toast_suppressed: false,
        },
      };
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Bulk grant' }));

    await waitFor(() => {
      expect(screen.getByTestId('medal-bulk-panel')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Bulk medal'), {
      target: { value: 'bronze_cluster' },
    });
    fireEvent.change(screen.getByLabelText('Bulk recipients'), {
      target: { value: 'Ace\nBob' },
    });

    expect(screen.getByTestId('medal-bulk-commit')).toBeDisabled();

    fireEvent.click(screen.getByTestId('medal-bulk-dry-run'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/medals/admin/bulk-grant', {
        medal_id: 'bronze_cluster',
        recipients: ['Ace', 'Bob'],
        reason: null,
        dry_run: true,
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId('medal-bulk-dry-run-summary')).toBeTruthy();
      expect(screen.getByTestId('medal-bulk-commit')).not.toBeDisabled();
    });

    fireEvent.click(screen.getByTestId('medal-bulk-commit'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/medals/admin/bulk-grant', {
        medal_id: 'bronze_cluster',
        recipients: ['Ace', 'Bob'],
        reason: null,
        dry_run: false,
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId('medal-bulk-grant-batch-id').textContent).toBe('batch-111');
    });
  });

  it('bulk dry-run invalid mix shows samples and keeps commit disabled when nothing grantable', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return {
          data: {
            total: 1,
            items: [{ id: 'bronze_cluster', name: 'Bronze Cluster', category: 'combat' }],
          },
        };
      }
      return { data: { players: [] } };
    });
    vi.mocked(api.post).mockResolvedValue({
      data: {
        dry_run: true,
        medal_id: 'bronze_cluster',
        valid_count: 0,
        invalid_count: 2,
        already_held_count: 0,
        grantable_count: 0,
        granted_count: 0,
        invalid_samples: [
          { input: 'nope', reason: 'unknown_username' },
          { input: 'bad-id', reason: 'unknown_player_id' },
        ],
        grant_batch_id: null,
        toast_suppressed: false,
      },
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Bulk grant' }));

    await waitFor(() => {
      expect(screen.getByLabelText('Bulk medal')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Bulk medal'), {
      target: { value: 'bronze_cluster' },
    });
    fireEvent.change(screen.getByLabelText('Bulk recipients'), {
      target: { value: 'nope,bad-id' },
    });
    fireEvent.click(screen.getByTestId('medal-bulk-dry-run'));

    await waitFor(() => {
      expect(screen.getByTestId('medal-bulk-invalid-samples')).toBeTruthy();
    });
    expect(screen.getByText(/unknown_username/)).toBeTruthy();
    expect(screen.getByText(/unknown_player_id/)).toBeTruthy();
    expect(screen.getByTestId('medal-bulk-commit')).toBeDisabled();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/api/v1/medals/admin/bulk-grant', {
      medal_id: 'bronze_cluster',
      recipients: ['nope', 'bad-id'],
      reason: null,
      dry_run: true,
    });
  });

  it('shows honest 404 on catalog tab when GS catalog route is absent', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        throw Object.assign(new Error('HTTP 404'), {
          response: { status: 404, data: { detail: 'Not Found' } },
        });
      }
      return { data: { players: [] } };
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Catalog' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/catalog route not found/i);
    });
    expect(screen.getByRole('alert').textContent).not.toMatch(/Failed to grant medal/i);
  });

  it('surfaces PLAYERS_ADJUST_REP scope denial on grant instead of generic failure', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.players.adjust_rep (PLAYERS_ADJUST_REP)' },
        },
      }),
    );

    render(<MedalAdmin />);

    await waitFor(() => {
      expect(screen.getByLabelText('Select player')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Select player'), { target: { value: 'p1' } });
    fireEvent.change(screen.getByLabelText('Medal'), { target: { value: 'bronze_cluster' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grant medal' }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });
    expect(mockToastError.mock.calls[0][0]).toMatch(/PLAYERS_ADJUST_REP|adjust_rep/i);
    expect(mockToastError.mock.calls[0][0]).not.toMatch(/^Grant failed$/);
  });

  it('surfaces admin rate-limit copy on grant 429', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
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
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429, data: { detail: 'Too Many Requests' } },
      }),
    );

    render(<MedalAdmin />);

    await waitFor(() => {
      expect(screen.getByLabelText('Select player')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Select player'), { target: { value: 'p1' } });
    fireEvent.change(screen.getByLabelText('Medal'), { target: { value: 'bronze_cluster' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grant medal' }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });
    expect(mockToastError.mock.calls[0][0]).toMatch(/rate limit/i);
  });

  it('loads player collection and surfaces awards + view_hidden audits', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return { data: { total: 0, items: [] } };
      }
      if (url.includes('/medals/admin/players/p1/collection')) {
        return {
          data: {
            player_id: 'p1',
            total: 1,
            view_hidden_medal_audits_written: 1,
            items: [
              {
                medal_id: 'special.orange_cat_society',
                name: 'Orange Cat Society',
                category: 'special',
                tier: null,
                awarded_at: '2026-08-21T00:00:00Z',
                awarded_via: 'admin',
                is_hidden_catalog: true,
                privacy_overridden: true,
                reason: 'verify',
              },
            ],
          },
        };
      }
      return { data: { players: [{ id: 'p1', username: 'Ace' }] } };
    });

    render(<MedalAdmin />);

    fireEvent.click(screen.getByRole('tab', { name: 'Player collection' }));

    await waitFor(() => {
      expect(screen.getByLabelText('Select collection player')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Select collection player'), {
      target: { value: 'p1' },
    });
    fireEvent.click(screen.getByTestId('medal-collection-load'));

    await waitFor(() => {
      expect(screen.getByTestId('medal-collection-table')).toBeTruthy();
    });
    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/medals/admin/players/p1/collection',
    );
    expect(screen.getByText('Orange Cat Society')).toBeTruthy();
    expect(screen.getByTestId('medal-collection-audits').textContent).toBe('1');
  });

  it('surfaces PLAYERS_VIEW scope denial on collection 403', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return { data: { total: 0, items: [] } };
      }
      if (url.includes('/collection')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 403'), {
            response: {
              status: 403,
              data: { detail: 'Missing scope admin.players.view (PLAYERS_VIEW)' },
            },
          }),
        );
      }
      return { data: { players: [{ id: 'p1', username: 'Ace' }] } };
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Player collection' }));

    await waitFor(() => {
      expect(screen.getByLabelText('Select collection player')).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText('Select collection player'), {
      target: { value: 'p1' },
    });
    fireEvent.click(screen.getByTestId('medal-collection-load'));

    await waitFor(() => {
      expect(screen.getByTestId('medal-collection-error')).toBeTruthy();
    });
    expect(screen.getByTestId('medal-collection-error').textContent).toMatch(
      /PLAYERS_VIEW|players\.view/i,
    );
    expect(screen.getByTestId('medal-collection-error').textContent).not.toMatch(
      /^Failed to load player medal collection$/,
    );
  });

  it('surfaces admin rate-limit copy on collection 429', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/medals/admin/catalog')) {
        return { data: { total: 0, items: [] } };
      }
      if (url.includes('/collection')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 429'), {
            response: { status: 429, data: { detail: 'Too Many Requests' } },
          }),
        );
      }
      return { data: { players: [{ id: 'p1', username: 'Ace' }] } };
    });

    render(<MedalAdmin />);
    fireEvent.click(screen.getByRole('tab', { name: 'Player collection' }));

    await waitFor(() => {
      expect(screen.getByLabelText('Select collection player')).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText('Select collection player'), {
      target: { value: 'p1' },
    });
    fireEvent.click(screen.getByTestId('medal-collection-load'));

    await waitFor(() => {
      expect(screen.getByTestId('medal-collection-error')).toBeTruthy();
    });
    expect(screen.getByTestId('medal-collection-error').textContent).toMatch(/rate limit/i);
  });
});
