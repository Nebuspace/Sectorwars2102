import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { GenesisDeviceTracking } from './GenesisDeviceTracking';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const genesisPayload = {
  devices: [
    {
      id: 'gd-1',
      name: 'Terraformer Alpha',
      status: 'active' as const,
      ownerId: 'p1',
      ownerName: 'Operator One',
      location: {
        type: 'ship' as const,
        id: 's1',
        name: 'ISS Probe',
        sectorId: 'sec-1',
        sectorName: 'Nexus',
      },
      powerLevel: 80,
      integrity: 95,
      chargeTime: 0,
      deploymentHistory: [],
      createdAt: '2026-08-01T00:00:00Z',
      lastActivity: '2026-08-16T12:00:00Z',
    },
  ],
  stats: {
    totalDevices: 1,
    activeDevices: 1,
    deployedThisWeek: 0,
    successRate: 100,
    averagePowerLevel: 80,
    topUsers: [
      {
        playerId: 'p1',
        playerName: 'Operator One',
        deviceCount: 1,
        successfulDeployments: 0,
      },
    ],
  },
  alerts: [],
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('GenesisDeviceTracking (LEG-150)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads genesis data via shared api and hydrates without not-implemented copy', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: genesisPayload });

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByText('Terraformer Alpha')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/colonization/genesis-devices');
    expect(screen.getByText('Genesis Device Tracking')).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('reports a 403 as a scope problem, never as unimplemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/REGIONS_VIEW|regions view|Access denied/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toMatch(/route not found|proxy/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 429 as an admin rate-limit, not bare HTTP 429 load failure', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/Failed to load Genesis device data \(HTTP 429\)/);
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2956)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<GenesisDeviceTracking />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error fetching Genesis device/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
    expect(alert).not.toMatch(/Failed to load Genesis device data/i);
  });
});
