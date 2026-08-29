import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import NotFound from './NotFound';

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { username: 'admin-smoke', mfaEnabled: false },
    logout: vi.fn(),
    isAuthenticated: true,
    isLoading: false,
  }),
}));

describe('NotFound (LEG-2604)', () => {
  it('renders honest 404 landmark copy for unknown admin routes', () => {
    render(
      <MemoryRouter initialEntries={['/this-route-does-not-exist']}>
        <NotFound />
      </MemoryRouter>
    );

    expect(
      screen.getByRole('heading', { name: 'Page Not Found', level: 1 })
    ).toBeInTheDocument();
    expect(
      screen.getByText('No admin route matches "/this-route-does-not-exist".')
    ).toBeInTheDocument();
    expect(
      screen.getByText("The page you requested doesn't exist or may have moved.")
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Return to Dashboard' })).toHaveAttribute(
      'href',
      '/dashboard'
    );
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });
});
