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
  current: { username: 'admin-operator', mfaEnabled: false } as {
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

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3810 Soft-ORDER — UserProfile MFA toggle TypeError/Network Error densify.
 * UserProfile has no exported error helpers; failures surface via embedded MFASetup.
 */
describe('UserProfile typeErrorHonesty densify (LEG-3810)', () => {
  beforeEach(() => {
    mockUser.current = { username: 'admin-operator', mfaEnabled: false };
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('MFA toggle TypeError surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
    expect(bodyText).toContain(GENERATE_FALLBACK);
  });

  it('MFA toggle Network Error surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
    expect(bodyText).toContain(GENERATE_FALLBACK);
  });

  it('MFA toggle Failed to fetch surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Failed to fetch'));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(GENERATE_FALLBACK)).toBeInTheDocument();
    });

    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
    expect(bodyText).toContain(GENERATE_FALLBACK);
  });

  it('preserves non-transport server detail on MFA generate failure', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 500, data: { detail: 'MFA already enabled for this account.' } },
    });

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText('MFA already enabled for this account.')).toBeInTheDocument();
    });

    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
  });
});
