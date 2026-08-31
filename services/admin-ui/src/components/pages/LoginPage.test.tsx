import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import LoginPage from './LoginPage';

const mockNavigate = vi.hoisted(() => vi.fn());
const mockLocation = vi.hoisted(() => ({
  pathname: '/login',
  state: null as { from?: { pathname: string } } | null,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => mockLocation,
  };
});

vi.mock('../auth/LoginForm', () => ({
  default: ({ onLoginSuccess }: { onLoginSuccess?: () => void }) => (
    <button type="button" onClick={() => onLoginSuccess?.()}>
      trigger-login
    </button>
  ),
}));

describe('LoginPage deep-link return (LEG-3125)', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockLocation.pathname = '/login';
    mockLocation.state = null;
  });

  it('renders portal headings', () => {
    render(<LoginPage />);
    expect(screen.getByRole('heading', { name: 'Sector Wars 2102' })).toBeInTheDocument();
    expect(screen.getByText('Admin Portal')).toBeInTheDocument();
  });

  it('navigates to /dashboard when no deep-link state is present', () => {
    render(<LoginPage />);
    fireEvent.click(screen.getByRole('button', { name: 'trigger-login' }));
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
  });

  it('returns to the protected deep link after login success', () => {
    mockLocation.state = { from: { pathname: '/universe' } };
    render(<LoginPage />);
    fireEvent.click(screen.getByRole('button', { name: 'trigger-login' }));
    expect(mockNavigate).toHaveBeenCalledWith('/universe', { replace: true });
  });
});
