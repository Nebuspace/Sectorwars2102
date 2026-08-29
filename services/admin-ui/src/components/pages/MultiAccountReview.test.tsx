import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

const sampleCluster = {
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
};

function mockClustersLoaded() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/clusters/cluster-1')) {
      return { data: sampleCluster };
    }
    if (url.includes('/clusters')) {
      return { data: [sampleCluster] };
    }
    return { data: [] };
  });
}

async function openDecideForm() {
  mockClustersLoaded();
  render(<MultiAccountReview />);
  await waitFor(() => {
    expect(screen.getByText(/2 members/i)).toBeTruthy();
  });
  fireEvent.click(screen.getByText(/2 members/i));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Confirm (enforce limits)' })).toBeTruthy();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm (enforce limits)' }));
}

describe('MultiAccountReview decide POST (LEG-2765)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('decide POST 403 surfaces scope denial, not bare Failed to record decision', async () => {
    await openDecideForm();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/multi-account/clusters/cluster-1/decide',
        expect.objectContaining({ decision: 'confirmed' }),
      );
    });

    const decideError = screen.getByRole('alert');
    expect(decideError.textContent).toMatch(/admin multi-account review scopes required|Access denied/i);
    expect(decideError.textContent).not.toMatch(/^Failed to record decision$/);
  });

  it('decide POST 429 surfaces rate-limit copy', async () => {
    await openDecideForm();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    const decideError = screen.getByRole('alert');
    expect(decideError.textContent).toMatch(/rate limit/i);
    expect(decideError.textContent).not.toMatch(/^Failed to record decision$/);
  });
});
