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
 * LEG-3810 Soft-ORDER — UserProfile MFA toggle TypeError/Network Error densify.
 * LEG-3991 Soft-ORDER — HTTP 403/429 densify via embedded MFASetup/formatAdminApiError.
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

  it('MFA toggle 403 surfaces access-denied copy without transport leak', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(ACCESS_DENIED)).toBeInTheDocument();
    });

    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
    expect(bodyText).toContain(ACCESS_DENIED);
    expect(bodyText).not.toMatch(/\b403\b/);
    expect(bodyText).not.toMatch(/HTTP 403/i);
  });

  it('MFA toggle 429 surfaces rate-limit copy without transport leak', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    const user = userEvent.setup();
    render(<UserProfile />);

    await user.click(screen.getByRole('button', { name: /Enable MFA/i }));

    await waitFor(() => {
      expect(screen.getByText(RATE_LIMIT)).toBeInTheDocument();
    });

    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
    expect(bodyText).toContain(RATE_LIMIT);
    expect(bodyText).not.toMatch(/\b429\b/);
    expect(bodyText).not.toMatch(/HTTP 429/i);
  });
});
