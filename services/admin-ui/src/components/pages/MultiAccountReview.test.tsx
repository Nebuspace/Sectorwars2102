import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
