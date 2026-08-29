import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import GalaxyGenerationHistory from './GalaxyGenerationHistory';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { error?: string }) =>
      opts?.error ? `${key}:${opts.error}` : key,
  }),
}));

const loadBangHistory = vi.fn();

vi.mock('../../../contexts/AdminContext', () => ({
  useAdmin: () => ({
    bangHistory: [],
    bangHistoryTotal: 0,
    loadBangHistory,
    isLoading: false,
  }),
}));

const httpError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status } });

describe('GalaxyGenerationHistory load errors (LEG-2713)', () => {
  beforeEach(() => {
    loadBangHistory.mockReset();
  });

  it('surfaces BANG_REGENERATE scope denial on history load 403', async () => {
    loadBangHistory.mockRejectedValue(httpError(403));

    render(<GalaxyGenerationHistory />);

    await waitFor(() => {
      expect(loadBangHistory).toHaveBeenCalledWith(0, 20);
    });
    expect(screen.getByText(/BANG_REGENERATE/i)).toBeTruthy();
    expect(screen.getByText(/Access denied/i).textContent).not.toMatch(
      /Failed to load history/i,
    );
  });

  it('surfaces rate-limit copy on history load 429', async () => {
    loadBangHistory.mockRejectedValue(httpError(429));

    render(<GalaxyGenerationHistory />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    expect(screen.getByText(/bang\.history\.loadFailed/i).textContent).not.toMatch(
      /HTTP 429/i,
    );
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2934)', async () => {
    loadBangHistory.mockRejectedValue(new TypeError('Failed to fetch'));

    render(<GalaxyGenerationHistory />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load history/i)).toBeTruthy();
    });
    const text = screen.getByText(/bang\.history\.loadFailed/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
