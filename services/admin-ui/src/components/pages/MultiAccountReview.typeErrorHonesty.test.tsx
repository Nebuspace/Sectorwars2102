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

/**
 * LEG-3669 Soft-ORDER — MultiAccountReview TypeError/Network Error densify.
 */
describe('MultiAccountReview typeErrorHonesty densify (LEG-3669)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on clusters load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load clusters/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load clusters/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on clusters load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load clusters/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load clusters/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on cluster detail GET without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/clusters/cluster-1')) {
        throw new Error('Network Error');
      }
      if (url.includes('/clusters')) {
        return { data: [sampleCluster] };
      }
      return { data: [] };
    });

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/2 members/i)).toBeTruthy();
    });
    fireEvent.click(screen.getByText(/2 members/i));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load cluster detail/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on cluster detail GET without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/clusters/cluster-1')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.includes('/clusters')) {
        return { data: [sampleCluster] };
      }
      return { data: [] };
    });

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/2 members/i)).toBeTruthy();
    });
    fireEvent.click(screen.getByText(/2 members/i));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load cluster detail/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on decide POST without leaking raw transport text', async () => {
    await openDecideForm();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to record decision/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on decide POST without leaking transport text', async () => {
    await openDecideForm();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to record decision/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
