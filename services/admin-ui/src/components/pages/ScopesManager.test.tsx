import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ScopesManager } from './ScopesManager';
import { api } from '../../utils/auth';

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null }),
}));

vi.mock('../../utils/auth', async () => {
  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  };
});

const holders = [
  {
    user_id: 'u1',
    username: 'alice',
    is_admin: true,
    scopes: [{ scope: 'admin.scopes.grant' }],
  },
];
const catalog = [
  { scope: 'admin.scopes.grant', description: 'Grant scopes' },
  { scope: 'admin.galaxy.manage', description: 'Manage galaxy' },
];

function renderScopes() {
  return render(
    <MemoryRouter>
      <ScopesManager />
    </MemoryRouter>
  );
}

describe('ScopesManager', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('shows a forbidden alert when the holders fetch returns 403', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 403, data: { detail: 'You lack admin.scopes.grant' } },
    });

    renderScopes();

    await waitFor(() =>
      expect(screen.getByText('You lack admin.scopes.grant')).toBeInTheDocument()
    );
    expect(screen.getByRole('alert')).toHaveClass('scopes-alert-forbidden');
  });

  it('renders holders after a successful load', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('holders')) return Promise.resolve({ data: holders });
      return Promise.resolve({ data: catalog });
    });

    renderScopes();

    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    expect(screen.getByText('1 scope')).toBeInTheDocument();
  });

  it('marks the last holder of a meta-scope as non-revocable in the UI', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('holders')) return Promise.resolve({ data: holders });
      return Promise.resolve({ data: catalog });
    });

    renderScopes();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    await user.click(screen.getByText('alice'));

    const revokeBtn = await screen.findByRole('button', { name: /revoke/i });
    expect(revokeBtn).toBeDisabled();
    expect(
      screen.getByText(/Last holder of this meta-scope/)
    ).toBeInTheDocument();
  });

  it('grants a scope and reloads holders on success', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('holders')) return Promise.resolve({ data: holders });
      return Promise.resolve({ data: catalog });
    });
    vi.mocked(api.post).mockResolvedValue({ data: {} });

    renderScopes();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    await user.click(screen.getByText('alice'));

    await user.selectOptions(
      screen.getByLabelText('Grant scope'),
      'admin.galaxy.manage'
    );
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/grant', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      })
    );
  });
});
