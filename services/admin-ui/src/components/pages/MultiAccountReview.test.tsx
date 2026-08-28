import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MultiAccountReview from './MultiAccountReview';
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

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('MultiAccountReview (LEG-1098 honesty banner)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: [] });
  });

  it('does not claim the detection sweep is unshipped', async () => {
    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(document.querySelector('.mar-honest-gap')).toBeTruthy();
    });

    const banner = document.querySelector('.mar-honest-gap')!.textContent ?? '';
    expect(banner.toLowerCase()).not.toContain('has not shipped');
    expect(banner).toMatch(/hourly/i);
    expect(banner).toMatch(/empty queue|no open clusters/i);
  });
  it('reports a 403 as scope denial on cluster load', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/Access denied|scope/i);
    });
    expect(document.body.textContent).not.toMatch(/Failed to load clusters$/);
  });

  it('reports a 429 as an admin rate-limit on cluster load', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });
});

describe('MultiAccountReview scope errors (LEG-968)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.multi_account.review'),
    );

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.multi_account\.review/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

const pendingCluster = {
  id: 'cluster-1',
  signal_summary: { shared_ip: true },
  severity: 'soft' as const,
  all_paid_subscribers: false,
  admin_decision: 'pending' as const,
  admin_decision_reason: null,
  admin_decision_at: null,
  admin_decision_by: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  member_count: 2,
  flags: [],
};

describe('MultiAccountReview cluster detail GET formatAdminApiError (LEG-2677)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/clusters/cluster-1')) {
        return Promise.resolve({ data: pendingCluster });
      }
      return Promise.resolve({ data: [pendingCluster] });
    });
  });

  async function selectCluster(user: ReturnType<typeof userEvent.setup>) {
    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/2 members/i)).toBeInTheDocument();
    });

    await user.click(screen.getByText(/2 members/i));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/multi-account/clusters/cluster-1',
      );
    });
  }

  it('surfaces scope detail on cluster detail GET 403', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/clusters/cluster-1')) {
        return Promise.reject(
          axiosError(403, 'Missing scope admin.multi_account.review'),
        );
      }
      return Promise.resolve({ data: [pendingCluster] });
    });
    const user = userEvent.setup();
    await selectCluster(user);

    await waitFor(() => {
      expect(
        screen.getByText(/Missing scope admin\.multi_account\.review/i),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to load cluster detail')).not.toBeInTheDocument();
  });

  it('surfaces rate-limit copy on cluster detail GET 429', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/clusters/cluster-1')) {
        return Promise.reject(axiosError(429));
      }
      return Promise.resolve({ data: [pendingCluster] });
    });
    const user = userEvent.setup();
    await selectCluster(user);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to load cluster detail')).not.toBeInTheDocument();
  });
});
