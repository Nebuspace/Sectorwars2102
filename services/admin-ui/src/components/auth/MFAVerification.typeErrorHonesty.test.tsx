import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MFAVerification, mfaVerificationError } from './MFAVerification';

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

/**
 * LEG-3807 Soft-ORDER — MFAVerification verify TypeError/Network Error densify.
 * LEG-3989 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError fallback.
 */
describe('mfaVerificationError formatter (LEG-3807)', () => {
  it('collapses TypeError Failed to fetch to operator-safe fallback', () => {
    const text = mfaVerificationError(new TypeError('Failed to fetch'));
    expect(text).toBe(VERIFY_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses axios Network Error to operator-safe fallback', () => {
    const text = mfaVerificationError(new Error('Network Error'));
    expect(text).toBe(VERIFY_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses Error Failed to fetch to operator-safe fallback', () => {
    const text = mfaVerificationError(new Error('Failed to fetch'));
    expect(text).toBe(VERIFY_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport verify errors', () => {
    expect(mfaVerificationError(new Error('Invalid MFA code'))).toBe('Invalid MFA code');
  });

  it('surfaces 403 access-denied copy without transport leak', () => {
    const text = mfaVerificationError(axiosError(403));
    expect(text).toBe(ACCESS_DENIED);
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
  });

  it('surfaces 429 rate-limit copy without transport leak', () => {
    const text = mfaVerificationError(axiosError(429));
    expect(text).toBe(RATE_LIMIT);
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
  });
});

describe('MFAVerification typeErrorHonesty densify (LEG-3807)', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('submit TypeError surfaces honest fallback without raw transport text', async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    render(<MFAVerification onVerify={onVerify} />);

    const input = screen.getByPlaceholderText('000000');
    await user.type(input, '123456');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByText(VERIFY_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(VERIFY_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('submit Network Error surfaces honest fallback without raw transport text', async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn().mockRejectedValue(new Error('Network Error'));
    render(<MFAVerification onVerify={onVerify} />);

    const input = screen.getByPlaceholderText('000000');
    await user.type(input, '123456');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByText(VERIFY_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(VERIFY_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server message on verify failure', async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn().mockRejectedValue(new Error('Invalid MFA code'));
    render(<MFAVerification onVerify={onVerify} />);

    const input = screen.getByPlaceholderText('000000');
    await user.type(input, '123456');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByText('Invalid MFA code')).toBeInTheDocument();
    });
  });

  it('submit 403 surfaces access-denied copy without transport leak', async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn().mockRejectedValue(axiosError(403));
    render(<MFAVerification onVerify={onVerify} />);

    const input = screen.getByPlaceholderText('000000');
    await user.type(input, '123456');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByText(ACCESS_DENIED)).toBeInTheDocument();
    });

    const text = screen.getByText(ACCESS_DENIED).textContent ?? '';
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
  });

  it('submit 429 surfaces rate-limit copy without transport leak', async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn().mockRejectedValue(axiosError(429));
    render(<MFAVerification onVerify={onVerify} />);

    const input = screen.getByPlaceholderText('000000');
    await user.type(input, '123456');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByText(RATE_LIMIT)).toBeInTheDocument();
    });

    const text = screen.getByText(RATE_LIMIT).textContent ?? '';
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
  });
});
