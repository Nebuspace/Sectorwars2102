import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

const emptySummary = {
  open: 0,
  contacted: 0,
  resolved: 0,
  total: 0,
  open_share: null,
};


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
 * LEG-3644 Soft-ORDER — ReEngagementQueuePanel TypeError/Network Error densify.
 * LEG-3919 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('ReEngagementQueuePanel typeErrorHonesty densify (LEG-3644)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on queue list fetch without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/summary')) {
        return { data: emptySummary };
      }
      throw new Error('Network Error');
    });

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load re-engagement queue/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on summary fetch without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/summary')) {
        throw new Error('Network Error');
      }
      return { data: { items: [], total: 0 } };
    });

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load re-engagement queue/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on queue list fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/summary')) {
        return { data: emptySummary };
      }
      throw new TypeError('Failed to fetch');
    });

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load re-engagement queue/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on summary fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/summary')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: { items: [], total: 0 } };
    });

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load re-engagement queue/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with PLAYERS_VIEW scope copy when queue list GET is denied', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/summary')) {
        return { data: emptySummary };
      }
      throw axiosError(403);
    });

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied|PLAYERS_VIEW|re-engagement queue/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on queue list GET', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/summary')) {
        return { data: emptySummary };
      }
      throw axiosError(429);
    });

    render(<ReEngagementQueuePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

});
