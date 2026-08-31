import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import UsersManager from './UsersManager';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin1', username: 'admin', is_admin: true } }),
}));

const mockAdmin = vi.hoisted(() => ({
  users: [] as Array<{
    id: string;
    username: string;
    email: string | null;
    is_active: boolean;
    is_admin: boolean;
    created_at: string;
    last_login: string | null;
  }>,
  loadUsers: vi.fn(),
  isLoading: false,
  error: null as string | null,
}));

vi.mock('../../contexts/AdminContext', () => ({
  useAdmin: () => mockAdmin,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

function renderUsers() {
  return render(
    <MemoryRouter>
      <UsersManager />
    </MemoryRouter>,
  );
}

/**
 * LEG-3415 Soft-ORDER — UsersManager TypeError/Network Error honesty densify.
 * formatAdminApiError collapses transport failures to operator-visible fallbacks.
 */
describe('UsersManager typeErrorHonesty densify (LEG-3415)', () => {
  beforeEach(() => {
    mockAdmin.users = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockAdmin.loadUsers.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load to honest fallback, not raw transport', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
    mockAdmin.loadUsers.mockImplementation(async () => {
      try {
        await api.get('/api/v1/admin/users');
      } catch (err) {
        mockAdmin.error = formatAdminApiError(err, {
          fallback: 'Failed to load user accounts',
          scopeHint: 'admin user management scopes required',
        });
      }
    });

    const { rerender } = renderUsers();
    await waitFor(() => expect(mockAdmin.loadUsers).toHaveBeenCalled());
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/users'),
    );
    rerender(
      <MemoryRouter>
        <UsersManager />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Failed to load user accounts/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load user accounts/i).textContent ?? '';
    expect(text).not.toMatch(/Network Error/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on load to honest fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
    mockAdmin.loadUsers.mockImplementation(async () => {
      try {
        await api.get('/api/v1/admin/users');
      } catch (err) {
        mockAdmin.error = formatAdminApiError(err, {
          fallback: 'Failed to load user accounts',
          scopeHint: 'admin user management scopes required',
        });
      }
    });

    const { rerender } = renderUsers();
    await waitFor(() => expect(mockAdmin.loadUsers).toHaveBeenCalled());
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/users'),
    );
    rerender(
      <MemoryRouter>
        <UsersManager />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Failed to load user accounts/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load user accounts/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on create POST to scope-aware fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    renderUsers();
    fireEvent.click(screen.getByRole('button', { name: /^Create User$/i }));
    fireEvent.change(screen.getByLabelText(/^Username$/i), {
      target: { value: 'newplayer' },
    });
    const submit = screen.getByRole('dialog').querySelector('button[type="submit"]');
    expect(submit).toBeTruthy();
    fireEvent.click(submit!);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByText(/Failed to create user/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to create user/i).textContent ?? '';
    expect(text).not.toMatch(/Network Error/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on create POST to operator fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    renderUsers();
    fireEvent.click(screen.getByRole('button', { name: /^Create User$/i }));
    fireEvent.change(screen.getByLabelText(/^Username$/i), {
      target: { value: 'newplayer' },
    });
    const submit = screen.getByRole('dialog').querySelector('button[type="submit"]');
    expect(submit).toBeTruthy();
    fireEvent.click(submit!);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByText(/Failed to create user/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to create user/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
