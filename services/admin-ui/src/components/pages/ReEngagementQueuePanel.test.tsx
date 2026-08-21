import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import ReEngagementQueuePanel from './ReEngagementQueuePanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
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

function openQueueMocks() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/summary')) {
      return {
        data: { open: 1, contacted: 0, resolved: 0, total: 1, open_share: 1 },
      };
    }
    return {
      data: {
        total: 1,
        items: [
          {
            id: 'q1',
            player_id: 'p1',
            player_nickname: 'AtRisk',
            signals: ['inactive_7d'],
            signal_detail: {},
            status: 'OPEN',
            computed_at: '2026-08-16T00:00:00Z',
            computed_day: 1,
            resolved_at: null,
          },
        ],
      },
    };
  });
}

describe('ReEngagementQueuePanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    mockToastError.mockReset();
  });

  it('loads OPEN queue + summary and marks contacted', async () => {
    const onSummaryChange = vi.fn();
    openQueueMocks();
    vi.mocked(api.patch).mockResolvedValue({ data: { id: 'q1', status: 'CONTACTED' } });

    render(<ReEngagementQueuePanel onSummaryChange={onSummaryChange} />);

    await waitFor(() => {
      expect(screen.getByText('AtRisk')).toBeTruthy();
    });
    expect(onSummaryChange).toHaveBeenCalledWith(
      expect.objectContaining({ open: 1, total: 1 })
    );

    fireEvent.click(screen.getByRole('button', { name: 'Mark contacted' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/re-engagement/q1', {
        status: 'CONTACTED',
      });
    });
  });

  it('surfaces PLAYERS_VIEW scope copy on load 403', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: {} },
      })
    );

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/PLAYERS_VIEW|Access denied/i);
    });
    expect(screen.getByRole('alert').textContent).not.toMatch(/^Failed to load re-engagement queue$/);
    expect(screen.getByText(/No rows for this filter/i)).toBeTruthy();
  });

  it('surfaces admin rate-limit copy on load 429', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429, data: {} },
      })
    );

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.getByRole('alert').textContent).not.toMatch(/^Failed to load re-engagement queue$/);
  });

  it('surfaces PLAYERS_ADJUST_REP scope copy on mutate 403', async () => {
    openQueueMocks();
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: {} },
      })
    );

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByText('AtRisk')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Mark contacted' }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });
    expect(mockToastError.mock.calls[0][0]).toMatch(/PLAYERS_ADJUST_REP|Access denied/i);
    expect(mockToastError.mock.calls[0][0]).not.toMatch(/^Failed to update status$/);
  });

  it('surfaces admin rate-limit copy on mutate 429', async () => {
    openQueueMocks();
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429, data: {} },
      })
    );

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByText('AtRisk')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Mark contacted' }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });
    expect(mockToastError.mock.calls[0][0]).toMatch(/rate limit/i);
  });
});
