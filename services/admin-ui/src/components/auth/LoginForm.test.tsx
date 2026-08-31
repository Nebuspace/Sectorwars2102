import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LoginForm from './LoginForm';

const mockLogin = vi.fn();
const mockVerifyMFA = vi.fn();
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ login: mockLogin, verifyMFA: mockVerifyMFA }),
}));

describe('LoginForm', () => {
  beforeEach(() => {
    mockLogin.mockReset();
    mockVerifyMFA.mockReset();
  });

  it('requires both fields before submitting', async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole('button', { name: /^login$/i }));
    expect(screen.getByText('Please enter both username and password')).toBeInTheDocument();
    expect(mockLogin).not.toHaveBeenCalled();
  });

  it('calls login and onLoginSuccess on a non-MFA success', async () => {
    const user = userEvent.setup();
    const onLoginSuccess = vi.fn();
    mockLogin.mockResolvedValue({ requiresMFA: false });
    render(<LoginForm onLoginSuccess={onLoginSuccess} />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith('admin', 'hunter2'));
    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalled());
  });

  it('switches to the MFA verification view when login requires MFA', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ requiresMFA: true, sessionToken: 'sess-1' });
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() =>
      expect(screen.getByText('Two-Factor Authentication')).toBeInTheDocument()
    );
  });

  it('shows "Invalid username or password" for a 401 login failure', async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValue({ response: { status: 401 } });
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    await waitFor(() =>
      expect(screen.getByText('Invalid username or password')).toBeInTheDocument()
    );
  });

  it('disables the submit button while a login is in flight', async () => {
    const user = userEvent.setup();
    let resolveLogin: (v: any) => void = () => {};
    mockLogin.mockReturnValue(new Promise((resolve) => { resolveLogin = resolve; }));
    render(<LoginForm />);

    await user.type(screen.getByLabelText('Username'), 'admin');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: /^login$/i }));

    expect(screen.getByRole('button', { name: /logging in/i })).toBeDisabled();
    resolveLogin({ requiresMFA: false });
  });

  it('surfaces formatAdminApiError fallback on login TypeError (LEG-3322)', async () => {
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
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });
});
