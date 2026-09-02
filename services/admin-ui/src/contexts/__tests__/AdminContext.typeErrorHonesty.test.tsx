import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AdminProvider, useAdmin } from '../AdminContext';
import { api } from '../../utils/auth';
import { createBangJob } from '../../services/bangGalaxyApi';

const mockUseAuth = vi.fn();
vi.mock('../AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('../../services/bangGalaxyApi', () => ({
  createBangJob: vi.fn(),
  listBangJobs: vi.fn(),
  wipeBangGalaxy: vi.fn(),
}));

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const minimalBangConfig = {
  seed: 1,
  sectors: 100,
  region_type: 'player_owned' as const,
};

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

function Probe() {
  const { error, loadAdminStats, loadGalaxyInfo, bangGalaxy } = useAdmin();
  return (
    <div>
      <span data-testid="error">{error ?? 'none'}</span>
      <button onClick={() => loadAdminStats()}>load-stats</button>
      <button onClick={() => void loadGalaxyInfo()}>load-galaxy-info</button>
      <button
        onClick={() => {
          void bangGalaxy(minimalBangConfig, 'Test Galaxy').catch(() => undefined);
        }}
      >
        bang-galaxy
      </button>
    </div>
  );
}

function renderProbe() {
  mockUseAuth.mockReturnValue({ user: { id: '1', is_admin: true }, token: 'tok' });
  return render(
    <AdminProvider>
      <Probe />
    </AdminProvider>,
  );
}

/**
 * LEG-3830 Soft-ORDER — AdminContext TypeError/Network Error densify.
 */
describe('AdminContext typeErrorHonesty densify (LEG-3830)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(createBangJob).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loadAdminStats TypeError surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    renderProbe();

    await user.click(screen.getByText('load-stats'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to load admin statistics'),
    );
    assertNoTransportLeak(screen.getByTestId('error').textContent ?? '');
  });

  it('loadAdminStats Network Error surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
    const user = userEvent.setup();
    renderProbe();

    await user.click(screen.getByText('load-stats'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to load admin statistics'),
    );
    assertNoTransportLeak(screen.getByTestId('error').textContent ?? '');
  });

  it('loadGalaxyInfo TypeError surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    renderProbe();

    await user.click(screen.getByText('load-galaxy-info'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to load galaxy information'),
    );
    assertNoTransportLeak(screen.getByTestId('error').textContent ?? '');
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/galaxy');
  });

  it('loadGalaxyInfo Network Error surfaces honest fallback without raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
    const user = userEvent.setup();
    renderProbe();

    await user.click(screen.getByText('load-galaxy-info'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to load galaxy information'),
    );
    assertNoTransportLeak(screen.getByTestId('error').textContent ?? '');
  });

  it('bangGalaxy TypeError surfaces honest fallback without raw transport text', async () => {
    vi.mocked(createBangJob).mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    renderProbe();

    await user.click(screen.getByText('bang-galaxy'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to start bang generation job'),
    );
    assertNoTransportLeak(screen.getByTestId('error').textContent ?? '');
  });

  it('bangGalaxy Network Error surfaces honest fallback without raw transport text', async () => {
    vi.mocked(createBangJob).mockRejectedValue(new Error('Network Error'));
    const user = userEvent.setup();
    renderProbe();

    await user.click(screen.getByText('bang-galaxy'));
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to start bang generation job'),
    );
    assertNoTransportLeak(screen.getByTestId('error').textContent ?? '');
  });
});
