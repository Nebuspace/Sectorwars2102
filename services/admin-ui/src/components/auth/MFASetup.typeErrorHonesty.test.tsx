import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MFASetup } from './MFASetup';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'admin' } }),
}));

const GENERATE_FALLBACK = 'Failed to generate MFA secret';
const VERIFY_FALLBACK = 'Verification failed';

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
  });

const ACCESS_DENIED =
  'Access denied — you lack the required admin scope for this action.';
const RATE_LIMIT =
  'Admin rate limit exceeded — wait a moment and try again.';

const happyGenerate = {
  secret: 'ABCDEFGHIJK',
  setup_url: 'otpauth://test',
  qr_code_data_url: 'data:image/png;base64,abc',
  message: 'ok',
};

/**
 * LEG-3784 Soft-ORDER — MFASetup generate/verify TypeError/Network Error densify.
 * LEG-3990 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError fallback.
 */
describe('MFASetup typeErrorHonesty densify (LEG-3784)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses TypeError on generate without leaking raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(GENERATE_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('collapses Network Error on generate without leaking raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(GENERATE_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail on generate failure', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 500, data: { detail: 'MFA already enabled for this account.' } },
    });

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('MFA already enabled for this account.')).toBeInTheDocument();
    });
  });

  it('generate 403 surfaces access-denied copy without transport leak', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText(ACCESS_DENIED)).toBeInTheDocument();
    });

    const text = screen.getByText(ACCESS_DENIED).textContent ?? '';
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
  });

  it('generate 429 surfaces rate-limit copy without transport leak', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText(RATE_LIMIT)).toBeInTheDocument();
    });

    const text = screen.getByText(RATE_LIMIT).textContent ?? '';
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
  });

  it('collapses TypeError on verify without leaking raw transport text', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: happyGenerate })
      .mockRejectedValueOnce(new TypeError('Failed to fetch'));

    const user = userEvent.setup();
    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /^next$/i }));

    const codeInput = screen.getByPlaceholderText('000000');
    await user.type(codeInput, '123456');
    await user.click(screen.getByRole('button', { name: /^verify$/i }));

    await waitFor(() => {
      expect(screen.getByText(VERIFY_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(VERIFY_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('collapses Failed to fetch on verify without leaking raw transport text', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: happyGenerate })
      .mockRejectedValueOnce(new Error('Failed to fetch'));

    const user = userEvent.setup();
    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /^next$/i }));

    const codeInput = screen.getByPlaceholderText('000000');
    await user.type(codeInput, '123456');
    await user.click(screen.getByRole('button', { name: /^verify$/i }));

    await waitFor(() => {
      expect(screen.getByText(VERIFY_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(VERIFY_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('verify 403 surfaces access-denied copy without transport leak', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: happyGenerate })
      .mockRejectedValueOnce(axiosError(403));

    const user = userEvent.setup();
    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /^next$/i }));

    const codeInput = screen.getByPlaceholderText('000000');
    await user.type(codeInput, '123456');
    await user.click(screen.getByRole('button', { name: /^verify$/i }));

    await waitFor(() => {
      expect(screen.getByText(ACCESS_DENIED)).toBeInTheDocument();
    });

    const text = screen.getByText(ACCESS_DENIED).textContent ?? '';
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
  });

  it('verify 429 surfaces rate-limit copy without transport leak', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: happyGenerate })
      .mockRejectedValueOnce(axiosError(429));

    const user = userEvent.setup();
    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /^next$/i }));

    const codeInput = screen.getByPlaceholderText('000000');
    await user.type(codeInput, '123456');
    await user.click(screen.getByRole('button', { name: /^verify$/i }));

    await waitFor(() => {
      expect(screen.getByText(RATE_LIMIT)).toBeInTheDocument();
    });

    const text = screen.getByText(RATE_LIMIT).textContent ?? '';
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
  });
});
