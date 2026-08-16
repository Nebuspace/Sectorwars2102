import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AdminActionLogPage, {
  REVIEW_QUEUE_STALE_ALARM_THRESHOLD,
} from './AdminActionLogPage';
import { api } from '../../utils/auth';

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'admin-1', username: 'ops' },
    isAuthenticated: true,
    logout: vi.fn(),
  }),
}));

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

function makeRow(id: string, stale: boolean) {
  return {
    id,
    admin_user_id: `admin-${id}`,
    scope_used: 'admin.scopes.grant',
    action: 'scope_grant',
    target_type: 'user',
    target_id: `target-${id}`,
    payload_snapshot: null,
    result: 'ok',
    failure_reason: null,
    reviewed_by: null,
    reviewed_at: null,
    at: '2026-01-01T00:00:00Z',
    stale,
  };
}

function mockReviewQueue(items: ReturnType<typeof makeRow>[]) {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url === '/api/v1/admin/audit/review-queue') {
      return Promise.resolve({
        data: {
          items,
          total: items.length,
          page: 1,
          limit: 50,
          pages: 1,
        },
      });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function renderReviewTab() {
  return render(
    <MemoryRouter initialEntries={['/audit?tab=review']}>
      <AdminActionLogPage />
    </MemoryRouter>
  );
}

describe('AdminActionLogPage review-queue sweep-test alarm (LEG-110)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('exports a provisional threshold ≤ 5', () => {
    expect(REVIEW_QUEUE_STALE_ALARM_THRESHOLD).toBeLessThanOrEqual(5);
    expect(REVIEW_QUEUE_STALE_ALARM_THRESHOLD).toBeGreaterThan(0);
  });

  it('shows the sweep alarm when stale count meets the provisional threshold', async () => {
    const staleRows = Array.from({ length: REVIEW_QUEUE_STALE_ALARM_THRESHOLD }, (_, i) =>
      makeRow(`stale-${i}`, true)
    );
    mockReviewQueue([...staleRows, makeRow('fresh-0', false)]);

    renderReviewTab();

    await waitFor(() => {
      expect(screen.getByTestId('review-queue-sweep-alarm')).toBeTruthy();
    });
    expect(screen.getByTestId('review-queue-sweep-alarm').textContent).toMatch(
      /Sweep-test alarm/i
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/audit/review-queue', {
      params: { page: 1, limit: 50 },
    });
  });

  it('hides the sweep alarm when stale count is below the provisional threshold', async () => {
    const below = Math.max(0, REVIEW_QUEUE_STALE_ALARM_THRESHOLD - 1);
    const staleRows = Array.from({ length: below }, (_, i) => makeRow(`stale-${i}`, true));
    mockReviewQueue([...staleRows, makeRow('fresh-0', false)]);

    renderReviewTab();

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Review queue' })).toHaveAttribute(
        'aria-selected',
        'true'
      );
      expect(screen.queryByText(/Loading actions/i)).toBeNull();
      expect(screen.getByText(/\d+ rows? · page/i)).toBeTruthy();
    });
    expect(screen.queryByTestId('review-queue-sweep-alarm')).toBeNull();
  });
});
