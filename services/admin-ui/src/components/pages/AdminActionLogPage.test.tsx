import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AdminActionLogPage, {
  REVIEW_QUEUE_STALE_ALARM_THRESHOLD,
} from './AdminActionLogPage';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function renderLog() {
  return render(
    <MemoryRouter>
      <AdminActionLogPage />
    </MemoryRouter>,
  );
}

describe('AdminActionLogPage scope errors (LEG-1039)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 ledger load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.audit.view'),
    );

    renderLog();

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.audit\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 ledger load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    renderLog();

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

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
    </MemoryRouter>,
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
      makeRow(`stale-${i}`, true),
    );
    mockReviewQueue([...staleRows, makeRow('fresh-0', false)]);

    renderReviewTab();

    await waitFor(() => {
      expect(screen.getByTestId('review-queue-sweep-alarm')).toBeTruthy();
    });
    expect(screen.getByTestId('review-queue-sweep-alarm').textContent).toMatch(
      /Sweep-test alarm/i,
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
        'true',
      );
      expect(screen.queryByText(/Loading actions/i)).toBeNull();
      expect(screen.getByText(/\d+ rows? · page/i)).toBeTruthy();
    });
    expect(screen.queryByTestId('review-queue-sweep-alarm')).toBeNull();
  });
});
