import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

vi.mock('../../contexts/AdminContext', () => ({
  useAdmin: () => ({
    users: [],
    loadUsers: vi.fn(),
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function renderUsers() {
  return render(
    <MemoryRouter>
      <UsersManager />
    </MemoryRouter>,
  );
}

describe('UsersManager scope errors (LEG-1040)', () => {
  beforeEach(() => {
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
