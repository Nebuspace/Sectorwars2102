import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import UsersManager from './UsersManager';
import { api } from '../../utils/auth';

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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

const samplePlayer = {
  id: 'user-2',
  username: 'testplayer',
  email: 'test@example.com',
  is_active: true,
  is_admin: false,
  created_at: '2026-01-01T00:00:00Z',
  last_login: null,
};

const sampleAdmin = {
  id: 'user-3',
  username: 'otheradmin',
  email: 'other@example.com',
  is_active: true,
  is_admin: true,
  created_at: '2026-01-01T00:00:00Z',
  last_login: null,
};

function renderUsers() {
  return render(
    <MemoryRouter>
      <UsersManager />
    </MemoryRouter>,
  );
}

describe('UsersManager scope errors (LEG-1040)', () => {
  beforeEach(() => {
    mockAdmin.users = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on create 403', async () => {
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.users.manage'),
    );

    renderUsers();
    fireEvent.click(screen.getByRole('button', { name: /^Create User$/i }));

    fireEvent.change(screen.getByLabelText(/^Username$/i), {
      target: { value: 'newplayer' },
    });
    const submit = screen.getByRole('dialog').querySelector('button[type="submit"]');
    expect(submit).toBeTruthy();
    fireEvent.click(submit!);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.users\.manage/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on create 429', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    renderUsers();
    fireEvent.click(screen.getByRole('button', { name: /^Create User$/i }));

    fireEvent.change(screen.getByLabelText(/^Username$/i), {
      target: { value: 'newplayer' },
    });
    const submit = screen.getByRole('dialog').querySelector('button[type="submit"]');
    expect(submit).toBeTruthy();
    fireEvent.click(submit!);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('UsersManager mutation errors (LEG-2623)', () => {
  beforeEach(() => {
    mockAdmin.users = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces formatAdminApiError on update PUT 403', async () => {
    const user = userEvent.setup();
    mockAdmin.users = [samplePlayer];
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.users.manage'),
    );

    renderUsers();
    await waitFor(() => expect(screen.getByText('testplayer')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/users/${samplePlayer.id}`,
        expect.objectContaining({ username: samplePlayer.username }),
      );
    });
    expect(screen.getByText(/Missing scope admin\.users\.manage/i)).toBeTruthy();
    expect(screen.queryByText('Failed to update user')).toBeNull();
  });

  it('surfaces rate-limit copy on update PUT 429', async () => {
    const user = userEvent.setup();
    mockAdmin.users = [samplePlayer];
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    renderUsers();
    await waitFor(() => expect(screen.getByText('testplayer')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    expect(screen.getByText(/rate limit/i)).toBeTruthy();
    expect(screen.queryByText('Failed to update user')).toBeNull();
  });

  it('surfaces formatAdminApiError on delete DELETE 403', async () => {
    const user = userEvent.setup();
    mockAdmin.users = [samplePlayer];
    vi.mocked(api.delete).mockRejectedValue(
      axiosError(403, 'Missing scope admin.users.manage'),
    );

    renderUsers();
    await waitFor(() => expect(screen.getByText('testplayer')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Delete$/i }));
    const confirmInput = screen
      .getByText(/Type the username/i)
      .closest('.modal-body')
      ?.querySelector('input');
    expect(confirmInput).toBeTruthy();
    await user.type(confirmInput!, samplePlayer.username);
    await user.click(screen.getByRole('button', { name: /^Delete User$/i }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith(`/api/v1/users/${samplePlayer.id}`);
    });
    expect(screen.getByText(/Missing scope admin\.users\.manage/i)).toBeTruthy();
    expect(screen.queryByText('Failed to delete user')).toBeNull();
  });

  it('surfaces rate-limit copy on delete DELETE 429', async () => {
    const user = userEvent.setup();
    mockAdmin.users = [samplePlayer];
    vi.mocked(api.delete).mockRejectedValue(axiosError(429));

    renderUsers();
    await waitFor(() => expect(screen.getByText('testplayer')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Delete$/i }));
    const confirmInput = screen
      .getByText(/Type the username/i)
      .closest('.modal-body')
      ?.querySelector('input');
    expect(confirmInput).toBeTruthy();
    await user.type(confirmInput!, samplePlayer.username);
    await user.click(screen.getByRole('button', { name: /^Delete User$/i }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalled();
    });
    expect(screen.getByText(/rate limit/i)).toBeTruthy();
    expect(screen.queryByText('Failed to delete user')).toBeNull();
  });

  it('surfaces formatAdminApiError on password reset PUT 403', async () => {
    const user = userEvent.setup();
    mockAdmin.users = [sampleAdmin];
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.users.manage'),
    );

    renderUsers();
    await waitFor(() => expect(screen.getByText('otheradmin')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Reset$/i }));
    await user.type(screen.getByLabelText(/^New Password$/i), 'newpass123');
    await user.click(screen.getByRole('button', { name: /^Reset Password$/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/users/${sampleAdmin.id}/password`,
        JSON.stringify('newpass123'),
        { headers: { 'Content-Type': 'application/json' } },
      );
    });
    expect(screen.getByText(/Missing scope admin\.users\.manage/i)).toBeTruthy();
    expect(screen.queryByText('Failed to reset password')).toBeNull();
  });

  it('surfaces rate-limit copy on password reset PUT 429', async () => {
    const user = userEvent.setup();
    mockAdmin.users = [sampleAdmin];
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    renderUsers();
    await waitFor(() => expect(screen.getByText('otheradmin')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Reset$/i }));
    await user.type(screen.getByLabelText(/^New Password$/i), 'newpass123');
    await user.click(screen.getByRole('button', { name: /^Reset Password$/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    expect(screen.getByText(/rate limit/i)).toBeTruthy();
    expect(screen.queryByText('Failed to reset password')).toBeNull();
  });
});
