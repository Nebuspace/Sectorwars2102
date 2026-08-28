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

const pendingClusterListItem = {
  id: 'cluster-1',
  signal_summary: { shared_ip: true },
  severity: 'hard' as const,
  all_paid_subscribers: false,
  admin_decision: 'pending' as const,
  admin_decision_reason: null,
  admin_decision_at: null,
  admin_decision_by: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  member_count: 2,
};

const pendingClusterDetail = {
  ...pendingClusterListItem,
  flags: [],
};

function mockSuccessfulClusterLoad() {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url.includes('/clusters/cluster-1')) {
      return Promise.resolve({ data: pendingClusterDetail });
    }
    if (url.includes('/clusters')) {
      return Promise.resolve({ data: [pendingClusterListItem] });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
}

async function submitPendingClusterDecision(user: ReturnType<typeof userEvent.setup>) {
  render(<MultiAccountReview />);
  await waitFor(() => expect(screen.getByText(/2 members/i)).toBeInTheDocument());
  await user.click(screen.getByText(/2 members/i));

  await waitFor(() => expect(screen.getByText('Record Ruling')).toBeInTheDocument());

  await user.click(
    screen.getByRole('button', { name: /Confirm \(enforce limits\)/i }),
  );
  await user.click(screen.getByRole('button', { name: 'Submit Ruling' }));
}

describe('MultiAccountReview decide mutation errors (LEG-2628)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockSuccessfulClusterLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces formatAdminApiError on decide POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.multi_account.review'),
    );

    await submitPendingClusterDecision(user);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/multi-account/clusters/cluster-1/decide',
        { decision: 'confirmed', reason: undefined },
      );
    });

    const alert = document.querySelector('.mar-decide-error');
    expect(alert).toBeTruthy();
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent(/Missing scope admin\.multi_account\.review/i);
    expect(alert).not.toHaveTextContent('Failed to record decision');
  });

  it('shows rate-limit copy on decide POST 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await submitPendingClusterDecision(user);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/multi-account/clusters/cluster-1/decide',
        { decision: 'confirmed', reason: undefined },
      );
    });

    const alert = document.querySelector('.mar-decide-error');
    expect(alert).toBeTruthy();
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent(/rate limit/i);
    expect(alert).not.toHaveTextContent('Failed to record decision');
  });
});
