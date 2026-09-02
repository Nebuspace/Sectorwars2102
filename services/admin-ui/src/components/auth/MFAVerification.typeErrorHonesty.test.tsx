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
}

/**
 * LEG-3807 Soft-ORDER — MFAVerification verify TypeError/Network Error densify.
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
});
