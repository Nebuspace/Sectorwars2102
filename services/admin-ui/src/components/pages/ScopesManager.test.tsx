import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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

const holdersWithRevocable = [
  {
    user_id: 'u1',
    username: 'alice',
    is_admin: true,
    scopes: [
      { scope: 'admin.scopes.grant' },
      { scope: 'admin.galaxy.manage' },
    ],
  },
];

const catalog = [
  { scope: 'admin.scopes.grant', description: 'Grant scopes' },
  { scope: 'admin.galaxy.manage', description: 'Manage galaxy' },
];

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function mockSuccessfulLoad(holderList = holders) {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url.includes('holders')) return Promise.resolve({ data: holderList });
    return Promise.resolve({ data: catalog });
  });
}

async function selectAliceForGrant(user: ReturnType<typeof userEvent.setup>) {
  renderScopes();
  await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
  await user.click(screen.getByText('alice'));
  await user.selectOptions(
    screen.getByLabelText('Grant scope'),
    'admin.galaxy.manage',
  );
}

async function openRevokeConfirm(user: ReturnType<typeof userEvent.setup>) {
  mockSuccessfulLoad(holdersWithRevocable);
  renderScopes();
  await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
  await user.click(screen.getByText('alice'));
  const enabledRevoke = screen
    .getAllByRole('button', { name: /^Revoke$/i })
    .find((btn) => !btn.hasAttribute('disabled'));
  expect(enabledRevoke).toBeTruthy();
  await user.click(enabledRevoke!);
  await waitFor(() =>
    expect(screen.getByText('Confirm revoke')).toBeInTheDocument(),
  );
}

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

  it('shows rate-limit copy when the holders fetch returns 429', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 429, data: { detail: 'Too Many Requests' } },
    });

    renderScopes();

    await waitFor(() =>
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument()
    );
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
    mockSuccessfulLoad();
    vi.mocked(api.post).mockResolvedValue({ data: {} });

    await selectAliceForGrant(user);
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/grant', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      })
    );
  });
});

describe('ScopesManager grant/revoke mutation errors (LEG-2627)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces formatAdminApiError on grant POST 403', async () => {
    const user = userEvent.setup();
    mockSuccessfulLoad();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.scopes.grant'),
    );

    await selectAliceForGrant(user);
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/grant', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      });
    });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/Missing scope admin\.scopes\.grant/i);
    expect(alert).not.toHaveTextContent('Grant failed');
  });

  it('shows rate-limit copy on grant POST 429', async () => {
    const user = userEvent.setup();
    mockSuccessfulLoad();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await selectAliceForGrant(user);
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/rate limit/i);
    expect(alert).not.toHaveTextContent('Grant failed');
  });

  it('surfaces honest fallback on grant POST TypeError/network collapse (LEG-2975)', async () => {
    const user = userEvent.setup();
    mockSuccessfulLoad();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    await selectAliceForGrant(user);
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/grant', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      });
    });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/Grant failed/i);
    expect(alert).not.toHaveTextContent(/Failed to fetch/i);
    expect(alert).not.toHaveTextContent(/TypeError/i);
  });

  it('surfaces formatAdminApiError on revoke POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.scopes.revoke'),
    );

    await openRevokeConfirm(user);
    const dialog = screen.getByRole('dialog');
    await user.click(
      within(dialog).getByRole('button', { name: /^Revoke$/i }),
    );

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/revoke', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      });
    });

    const alerts = screen.getAllByRole('alert');
    const actionAlert = alerts.find((el) =>
      /Missing scope admin\.scopes\.revoke/i.test(el.textContent ?? ''),
    );
    expect(actionAlert).toBeTruthy();
    expect(actionAlert).not.toHaveTextContent('Revoke failed');
  });

  it('shows rate-limit copy on revoke POST 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await openRevokeConfirm(user);
    const dialog = screen.getByRole('dialog');
    await user.click(
      within(dialog).getByRole('button', { name: /^Revoke$/i }),
    );

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/revoke', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      });
    });

    const alerts = screen.getAllByRole('alert');
    const actionAlert = alerts.find((el) => /rate limit/i.test(el.textContent ?? ''));
    expect(actionAlert).toBeTruthy();
    expect(actionAlert).not.toHaveTextContent('Revoke failed');
  });
});

describe('ScopesManager axios Network Error densify (LEG-3509)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on initial load to honest fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    renderScopes();

    await waitFor(() => {
      expect(api.get).toHaveBeenCalled();
    });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/Failed to load scope holders/i);
    expect(alert).not.toHaveTextContent('Network Error');
    expect(alert.textContent ?? '').not.toMatch(/Network Error/i);
  });

  it('collapses axios-shaped Network Error on grant POST to honest fallback', async () => {
    const user = userEvent.setup();
    mockSuccessfulLoad();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    await selectAliceForGrant(user);
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/grant', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      });
    });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/Grant failed/i);
    expect(alert).not.toHaveTextContent('Network Error');
    expect(alert.textContent ?? '').not.toMatch(/Network Error/i);
  });

  it('collapses axios-shaped Network Error on revoke POST to honest fallback', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    await openRevokeConfirm(user);
    const dialog = screen.getByRole('dialog');
    await user.click(
      within(dialog).getByRole('button', { name: /^Revoke$/i }),
    );

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/scopes/revoke', {
        user_id: 'u1',
        scope: 'admin.galaxy.manage',
      });
    });

    const alerts = screen.getAllByRole('alert');
    const actionAlert = alerts.find((el) => /Revoke failed/i.test(el.textContent ?? ''));
    expect(actionAlert).toBeTruthy();
    expect(actionAlert).not.toHaveTextContent('Network Error');
    expect(actionAlert?.textContent ?? '').not.toMatch(/Network Error/i);
  });
});
