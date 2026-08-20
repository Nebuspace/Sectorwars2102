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

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

describe('ReEngagementQueuePanel', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
  });

  it('loads OPEN queue + summary and marks contacted', async () => {
    const onSummaryChange = vi.fn();
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
});
