import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LoginForm, { loginApiError } from './LoginForm';

const mockLogin = vi.fn();
const mockVerifyMFA = vi.fn();
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ login: mockLogin, verifyMFA: mockVerifyMFA }),
}));

const NETWORK_FALLBACK =
  'Login failed — unable to reach the gameserver. Check your connection and try again.';

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
 * LEG-3783 Soft-ORDER — LoginForm TypeError/Network Error densify.
 * LEG-3988 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError fallback.
 */
describe('loginApiError formatter (LEG-3783)', () => {
  it('collapses TypeError Failed to fetch to operator-safe fallback', () => {
    const text = loginApiError(new TypeError('Failed to fetch'));
    expect(text).toBe(NETWORK_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses axios Network Error / Failed to fetch to operator-safe fallback', () => {
    expect(loginApiError(new Error('Network Error'))).toBe(NETWORK_FALLBACK);
    expect(loginApiError(new Error('Failed to fetch'))).toBe(NETWORK_FALLBACK);
    assertNoTransportLeak(loginApiError(new Error('Network Error')));
  });

  it('preserves 401 invalid-credentials copy', () => {
    expect(loginApiError({ response: { status: 401 } })).toBe(
      'Invalid username or password',
    );
  });

  it('preserves 400 invalid-request copy', () => {
    expect(loginApiError({ response: { status: 400 } })).toBe(
      'Invalid login request. Please check your input.',
    );
  });

  it('preserves non-transport server detail', () => {
    expect(
      loginApiError({
        response: { status: 500, data: { detail: 'Account locked by admin.' } },
      }),
    ).toBe('Account locked by admin.');
  });

  it('surfaces 403 access-denied copy without transport leak', () => {
    const text = loginApiError(axiosError(403));
    expect(text).toBe(ACCESS_DENIED);
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
  });

  it('surfaces 429 rate-limit copy without transport leak', () => {
    const text = loginApiError(axiosError(429));
    expect(text).toBe(RATE_LIMIT);
    assertNoTransportLeak(text);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
  });
});

describe('LoginForm typeErrorHonesty densify (LEG-3783)', () => {
  beforeEach(() => {
    mockLogin.mockReset();
    mockVerifyMFA.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('submit TypeError surfaces honest fallback without raw transport text', async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValue(new TypeError('Failed to fetch'));
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Login failed/i)).toBeInTheDocument();
    });

    const alert = screen.getByText(/Login failed/i).textContent ?? '';
    expect(alert).toBe(NETWORK_FALLBACK);
    assertNoTransportLeak(alert);
  });

  it('submit Network Error surfaces honest fallback without raw transport text', async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValue(new Error('Network Error'));
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Login failed/i)).toBeInTheDocument();
    });

    const alert = screen.getByText(/Login failed/i).textContent ?? '';
    expect(alert).toBe(NETWORK_FALLBACK);
    assertNoTransportLeak(alert);
  });

  it('401 path unchanged on invalid credentials', async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValue({ response: { status: 401 } });
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() => {
      expect(screen.getByText('Invalid username or password')).toBeInTheDocument();
    });
  });

  it('submit 403 surfaces access-denied copy without transport leak', async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValue(axiosError(403));
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() => {
      expect(screen.getByText(ACCESS_DENIED)).toBeInTheDocument();
    });

    const alert = screen.getByText(ACCESS_DENIED).textContent ?? '';
    assertNoTransportLeak(alert);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
  });

  it('submit 429 surfaces rate-limit copy without transport leak', async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValue(axiosError(429));
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() => {
      expect(screen.getByText(RATE_LIMIT)).toBeInTheDocument();
    });

    const alert = screen.getByText(RATE_LIMIT).textContent ?? '';
    assertNoTransportLeak(alert);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
  });
});
