import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AdminActionLogPage from './AdminActionLogPage';
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

/**
 * LEG-3453 Soft-ORDER — AdminActionLogPage TypeError/Network Error honesty densify.
 */
describe('AdminActionLogPage typeErrorHonesty densify (LEG-3453)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  function renderLog() {
    return render(
      <MemoryRouter>
        <AdminActionLogPage />
      </MemoryRouter>,
    );
  }

  it('collapses axios Network Error on ledger load to action-log fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    renderLog();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load admin action log/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on ledger load to action-log fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    renderLog();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to load admin action log/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});
