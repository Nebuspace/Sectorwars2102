import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GalaxyGenerationForm from './GalaxyGenerationForm';
import { previewBangConfig } from '../../../services/bangGalaxyApi';
import { adminHttpErrorMessage } from '../../../utils/adminHttpError';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { error?: string }) =>
      opts?.error ? `${key}:${opts.error}` : key,
  }),
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'tok' }),
}));

const bangGalaxy = vi.fn();
vi.mock('../../../contexts/AdminContext', () => ({
  useAdmin: () => ({ bangGalaxy }),
}));

vi.mock('../../../services/bangGalaxyApi', () => ({
  previewBangConfig: vi.fn(),
}));

const PREVIEW_FALLBACK = 'Preview failed';
const SUBMIT_FALLBACK = 'Submit failed';

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
 * LEG-3808 Soft-ORDER — GalaxyGenerationForm preview/commit TypeError/Network Error densify.
 * LEG-4050 Soft-ORDER — HTTP 429 densify (invent=0).
 */
describe('adminHttpErrorMessage formatter (LEG-3808 / LEG-4050)', () => {
  it('collapses TypeError Failed to fetch to preview fallback', () => {
    const text = adminHttpErrorMessage(
      new TypeError('Failed to fetch'),
      PREVIEW_FALLBACK,
      'BANG_REGENERATE',
    );
    expect(text).toBe(PREVIEW_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses Network Error to submit fallback', () => {
    const text = adminHttpErrorMessage(new Error('Network Error'), SUBMIT_FALLBACK, 'BANG_REGENERATE');
    expect(text).toBe(SUBMIT_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('preserves BANG_REGENERATE denial on 403', () => {
    expect(
      adminHttpErrorMessage(
        axiosError(403),
        PREVIEW_FALLBACK,
        'BANG_REGENERATE',
      ),
    ).toMatch(/BANG_REGENERATE/i);
  });

  it('surfaces 429 as admin rate-limit copy', () => {
    const text = adminHttpErrorMessage(axiosError(429), PREVIEW_FALLBACK, 'BANG_REGENERATE');
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});

describe('GalaxyGenerationForm typeErrorHonesty densify (LEG-3808 / LEG-4050)', () => {
  beforeEach(() => {
    vi.mocked(previewBangConfig).mockReset();
    bangGalaxy.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('preview TypeError surfaces honest fallback without raw transport text', async () => {
    vi.mocked(previewBangConfig).mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: /preview/i }));

    await waitFor(() => {
      expect(screen.getByText(PREVIEW_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(PREVIEW_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('preview Network Error surfaces honest fallback without raw transport text', async () => {
    vi.mocked(previewBangConfig).mockRejectedValue(new Error('Network Error'));
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: /preview/i }));

    await waitFor(() => {
      expect(screen.getByText(PREVIEW_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(PREVIEW_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('preview 429 surfaces admin rate-limit copy without raw transport text', async () => {
    vi.mocked(previewBangConfig).mockRejectedValue(axiosError(429));
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: /preview/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });

    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('commit TypeError surfaces honest fallback without raw transport text', async () => {
    bangGalaxy.mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: 'bang.form.actions.commit' }));

    await waitFor(() => {
      expect(screen.getByText(SUBMIT_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(SUBMIT_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('commit Network Error surfaces honest fallback without raw transport text', async () => {
    bangGalaxy.mockRejectedValue(new Error('Network Error'));
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: 'bang.form.actions.commit' }));

    await waitFor(() => {
      expect(screen.getByText(SUBMIT_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(SUBMIT_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('commit 429 surfaces admin rate-limit copy without raw transport text', async () => {
    bangGalaxy.mockRejectedValue(axiosError(429));
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: 'bang.form.actions.commit' }));

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
