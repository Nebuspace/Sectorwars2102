import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import GalaxyGenerationHistory from './GalaxyGenerationHistory';
import { adminHttpErrorMessage } from '../../../utils/adminHttpError';

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

const HISTORY_FALLBACK = 'Failed to load history';

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3809 Soft-ORDER — GalaxyGenerationHistory load TypeError/Network Error densify.
 * LEG-4051 Soft-ORDER — HTTP 429 densify (invent=0).
 */
describe('adminHttpErrorMessage formatter (LEG-3809 / LEG-4051)', () => {
  it('collapses TypeError Failed to fetch to history load fallback', () => {
    const text = adminHttpErrorMessage(
      new TypeError('Failed to fetch'),
      HISTORY_FALLBACK,
      'BANG_REGENERATE',
    );
    expect(text).toBe(HISTORY_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses axios Network Error to history load fallback', () => {
    const text = adminHttpErrorMessage(
      new Error('Network Error'),
      HISTORY_FALLBACK,
      'BANG_REGENERATE',
    );
    expect(text).toBe(HISTORY_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('preserves BANG_REGENERATE denial on 403', () => {
    expect(
      adminHttpErrorMessage(
        axiosError(403),
        HISTORY_FALLBACK,
        'BANG_REGENERATE',
      ),
    ).toMatch(/BANG_REGENERATE/i);
  });

  it('surfaces 429 as admin rate-limit copy', () => {
    const text = adminHttpErrorMessage(axiosError(429), HISTORY_FALLBACK, 'BANG_REGENERATE');
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});

describe('GalaxyGenerationHistory typeErrorHonesty densify (LEG-3809 / LEG-4051)', () => {
  beforeEach(() => {
    loadBangHistory.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('load TypeError surfaces honest fallback without raw transport text', async () => {
    loadBangHistory.mockRejectedValue(new TypeError('Failed to fetch'));

    render(<GalaxyGenerationHistory />);

    await waitFor(() => {
      expect(loadBangHistory).toHaveBeenCalledWith(0, 20);
    });

    const text = screen.getByText(/bang\.history\.loadFailed/i).textContent ?? '';
    expect(text).toMatch(/Failed to load history/i);
    assertNoTransportLeak(text);
  });

  it('load Network Error surfaces honest fallback without raw transport text', async () => {
    loadBangHistory.mockRejectedValue(new Error('Network Error'));

    render(<GalaxyGenerationHistory />);

    await waitFor(() => {
      expect(loadBangHistory).toHaveBeenCalledWith(0, 20);
    });

    const text = screen.getByText(/bang\.history\.loadFailed/i).textContent ?? '';
    expect(text).toMatch(/Failed to load history/i);
    assertNoTransportLeak(text);
  });

  it('load 429 surfaces admin rate-limit copy without raw transport text', async () => {
    loadBangHistory.mockRejectedValue(axiosError(429));

    render(<GalaxyGenerationHistory />);

    await waitFor(() => {
      expect(loadBangHistory).toHaveBeenCalledWith(0, 20);
    });

    const text = screen.getByText(/bang\.history\.loadFailed/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
