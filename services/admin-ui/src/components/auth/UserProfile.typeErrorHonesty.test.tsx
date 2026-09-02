import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UserProfile from './UserProfile';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

const mockUser = vi.hoisted(() => ({
  current: { username: 'admin', mfaEnabled: false } as {
    username: string;
    mfaEnabled: boolean;
  } | null,
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: mockUser.current }),
}));

vi.mock('./LogoutButton', () => ({
  default: () => <button type="button">Logout</button>,
}));

const GENERATE_FALLBACK = 'Failed to generate MFA secret';
const VERIFY_FALLBACK = 'Verification failed';

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

const happyGenerate = {
  secret: 'ABCDEFGHIJK',
  setup_url: 'otpauth://test',
  qr_code_data_url: 'data:image/png;base64,abc',
  message: 'ok',
};

/**
 * LEG-3810 Soft-ORDER — UserProfile MFA toggle TypeError/Network Error densify.
 */
describe('UserProfile typeErrorHonesty densify (LEG-3810)', () => {
  beforeEach(() => {
    mockUser.current = { username: 'admin', mfaEnabled: false };
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses TypeError on MFA generate without leaking raw transport text', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(GENERATE_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('collapses Network Error on MFA generate without leaking raw transport text', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const text = screen.getByText(GENERATE_FALLBACK).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail on MFA generate failure', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 500, data: { detail: 'MFA already enabled for this account.' } },
    });

    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText('MFA already enabled for this account.')).toBeInTheDocument();
    });
  });

  it('collapses TypeError on MFA verify without leaking raw transport text', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: happyGenerate })
      .mockRejectedValueOnce(new TypeError('Failed to fetch'));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

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

  it('collapses Failed to fetch on MFA verify without leaking raw transport text', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: happyGenerate })
      .mockRejectedValueOnce(new Error('Failed to fetch'));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

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
});
