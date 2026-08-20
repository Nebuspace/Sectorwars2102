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
