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

  it('surfaces honest fallback on load TypeError/network collapse (LEG-3011)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /Failed to load re-engagement queue/i,
      );
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
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

describe('ReEngagementQueuePanel axios Network Error densify (LEG-3535)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    mockToastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on queue load', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load re-engagement queue/i);
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses axios-shaped Network Error on status mutate', async () => {
    openQueueMocks();
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByText('AtRisk')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Mark contacted' }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });

    const msg = String(mockToastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update status/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });
});
