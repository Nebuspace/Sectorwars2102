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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3669 Soft-ORDER — MultiAccountReview TypeError/Network Error densify.
 * LEG-3904 Soft-ORDER — 403/429 HTTP honesty densify.
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

  it('surfaces 403 with multi-account review scope copy when clusters GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/Access denied|multi-account review scopes/i)).toBeTruthy();
    });
    const text =
      screen.getByText(/Access denied|multi-account review scopes/i).textContent ?? '';
    expect(text).toMatch(/Access denied|multi-account review scopes/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on clusters GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<MultiAccountReview />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
