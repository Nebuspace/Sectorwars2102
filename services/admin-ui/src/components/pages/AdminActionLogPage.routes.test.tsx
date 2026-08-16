import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Navigate, Route, Routes } from 'react-router-dom';
import ProtectedRoute from '../auth/ProtectedRoute';
import { AdminActionLogPage } from './AdminActionLogPage';
import { api } from '../../utils/auth';

const mockUseAuth = vi.fn();
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

function renderReviewQueueHarness(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div>Login page</div>} />
        <Route
          path="/review-queue"
          element={
            <ProtectedRoute>
              <Navigate to="/audit?tab=review" replace />
            </ProtectedRoute>
          }
        />
        <Route
          path="/audit"
          element={
            <ProtectedRoute>
              <AdminActionLogPage />
            </ProtectedRoute>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

describe('LEG-77 review-queue route alias', () => {
  beforeEach(() => {
    mockUseAuth.mockReset();
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('redirects unauthenticated /review-queue to login', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: false, isLoading: false });
    renderReviewQueueHarness('/review-queue');
    expect(screen.getByText('Login page')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Review queue' })).not.toBeInTheDocument();
  });

  it('authenticated /review-queue resolves to the review-queue experience', async () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: true, isLoading: false });
    vi.mocked(api.get).mockResolvedValue({
      data: {
        items: [
          {
            id: 'a1',
            action: 'scope_grant',
            at: '2026-08-16T12:00:00Z',
            stale: false,
          },
        ],
        total: 1,
        page: 1,
        limit: 50,
        pages: 1,
      },
    });

    renderReviewQueueHarness('/review-queue');

    await waitFor(() =>
      expect(
        screen.getByText(/HIGH_IMPACT unreviewed actions/i)
      ).toBeInTheDocument()
    );
    expect(screen.getByRole('tab', { name: 'Review queue' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/audit/review-queue', {
      params: { page: 1, limit: 50 },
    });
    await waitFor(() => expect(screen.getByText('scope_grant')).toBeInTheDocument());
  });

  it('shows authorization failure when review-queue API returns 403', async () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: true, isLoading: false });
    vi.mocked(api.get).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You lack admin.audit.view' },
      },
    });

    renderReviewQueueHarness('/review-queue');

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('You lack admin.audit.view')
    );
  });
});
