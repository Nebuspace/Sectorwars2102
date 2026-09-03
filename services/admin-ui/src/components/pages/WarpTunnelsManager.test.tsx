import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WarpTunnelsManager from './WarpTunnelsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

const sampleTunnel = {
  id: 'tunnel-1',
  name: 'Test Tunnel',
  origin_sector_id: 1,
  destination_sector_id: 2,
  origin_sector_name: 'Origin',
  destination_sector_name: 'Dest',
  is_active: true,
  is_bidirectional: true,
  stability: 0.9,
  energy_cost: 100,
  travel_time: 5,
  max_ship_size: 'MEDIUM',
  total_traversals: 0,
};

function mockSuccessfulLoad() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (String(url).includes('/warp-tunnels')) {
      return { data: { warp_tunnels: [sampleTunnel] } };
    }
    if (String(url).includes('/sectors')) {
      return { data: { sectors: [], total: 0 } };
    }
    return { data: {} };
  });
}

describe('WarpTunnelsManager scope errors (LEG-966)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.universe.warp'),
    );

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.universe\.warp/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2949)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch warp tunnels/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to fetch warp tunnels/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toBe('Failed to fetch');
  });

  it('collapses axios-shaped Network Error to warp-tunnels fallback (LEG-3347)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<WarpTunnelsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch warp tunnels/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to fetch warp tunnels/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('WarpTunnelsManager mutation errors (LEG-2611)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
    toastError.mockReset();
    mockSuccessfulLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces formatAdminApiError on maintenance PUT 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.universe.warp'),
    );

    render(<WarpTunnelsManager />);
    await waitFor(() => expect(screen.getByText('Test Tunnel')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Maintain$/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/warp-tunnels/${sampleTunnel.id}`,
        { status: 'MAINTENANCE' },
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/Missing scope admin\.universe\.warp/i),
    );
    expect(toastError).not.toHaveBeenCalledWith(expect.stringMatching(/^Failed to update tunnel: update failed$/));
  });

  it('surfaces rate-limit copy on maintenance PUT 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    render(<WarpTunnelsManager />);
    await waitFor(() => expect(screen.getByText('Test Tunnel')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Maintain$/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
  });

  it('surfaces formatAdminApiError on delete DELETE 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.delete).mockRejectedValue(
      axiosError(403, 'Missing scope admin.universe.warp'),
    );

    render(<WarpTunnelsManager />);
    await waitFor(() => expect(screen.getByText('Test Tunnel')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Delete$/i }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith(
        `/api/v1/admin/warp-tunnels/${sampleTunnel.id}`,
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/Missing scope admin\.universe\.warp/i),
    );
    expect(toastError).not.toHaveBeenCalledWith(expect.stringMatching(/^Failed to delete tunnel: delete failed$/));
  });

  it('surfaces rate-limit copy on delete DELETE 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.delete).mockRejectedValue(axiosError(429));

    render(<WarpTunnelsManager />);
    await waitFor(() => expect(screen.getByText('Test Tunnel')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Delete$/i }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
  });
});

describe('WarpTunnelsManager modal save errors (LEG-2761)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    toastError.mockReset();
    mockSuccessfulLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces formatAdminApiError on modal save PUT 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.universe.warp'),
    );

    render(<WarpTunnelsManager />);
    await waitFor(() => expect(screen.getByText('Test Tunnel')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await waitFor(() => expect(screen.getByRole('heading', { name: /Edit Tunnel: Test Tunnel/i })).toBeTruthy());

    const energyInput = screen.getByDisplayValue('100');
    await user.clear(energyInput);
    await user.type(energyInput, '150');
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/warp-tunnels/${sampleTunnel.id}`,
        expect.objectContaining({ energy_cost: 150 }),
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/Missing scope admin\.universe\.warp/i),
    );
    expect(toastError).not.toHaveBeenCalledWith(expect.stringMatching(/^Failed to update tunnel: update failed$/));
  });

  it('surfaces rate-limit copy on modal save PUT 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    render(<WarpTunnelsManager />);
    await waitFor(() => expect(screen.getByText('Test Tunnel')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await waitFor(() => expect(screen.getByRole('heading', { name: /Edit Tunnel: Test Tunnel/i })).toBeTruthy());

    const energyInput = screen.getByDisplayValue('100');
    await user.clear(energyInput);
    await user.type(energyInput, '200');
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/warp-tunnels/${sampleTunnel.id}`,
        expect.objectContaining({ energy_cost: 200 }),
      );
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
  });
});

describe('WarpTunnelsManager Holding badge from sector has_pirate_holding (LEG-4201)', () => {
  const mockListLoad = (sectors: Array<Record<string, unknown>>) => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/warp-tunnels')) {
        return { data: { warp_tunnels: [sampleTunnel] } };
      }
      if (String(url).includes('/sectors')) {
        return { data: { sectors, total: sectors.length } };
      }
      return { data: {} };
    });
  };

  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a Holding badge when the origin sector has has_pirate_holding true', async () => {
    mockListLoad([
      { id: 'sec-origin', sector_id: 1, name: 'Origin', has_pirate_holding: true },
      { id: 'sec-dest', sector_id: 2, name: 'Dest', has_pirate_holding: false },
    ]);

    render(<WarpTunnelsManager />);

    expect(await screen.findByText('Test Tunnel')).toBeTruthy();
    expect(screen.getByTitle('Pirate Holding')).toHaveTextContent('Holding');
    const urls = vi.mocked(api.get).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes('pirate-holdings'))).toBe(false);
  });

  it('shows a Holding badge when the destination sector has has_pirate_holding true', async () => {
    mockListLoad([
      { id: 'sec-origin', sector_id: 1, name: 'Origin', has_pirate_holding: false },
      { id: 'sec-dest', sector_id: 2, name: 'Dest', has_pirate_holding: true },
    ]);

    render(<WarpTunnelsManager />);

    expect(await screen.findByText('Test Tunnel')).toBeTruthy();
    expect(screen.getByTitle('Pirate Holding')).toHaveTextContent('Holding');
    const urls = vi.mocked(api.get).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes('pirate-holdings'))).toBe(false);
  });

  it('does not show a Holding badge when origin and destination flags are false', async () => {
    mockListLoad([
      { id: 'sec-origin', sector_id: 1, name: 'Origin', has_pirate_holding: false },
      { id: 'sec-dest', sector_id: 2, name: 'Dest', has_pirate_holding: false },
    ]);

    render(<WarpTunnelsManager />);

    expect(await screen.findByText('Test Tunnel')).toBeTruthy();
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
    expect(screen.queryByText('Holding')).toBeNull();
  });

  it('does not show a Holding badge when has_pirate_holding is omitted on both ends', async () => {
    mockListLoad([
      { id: 'sec-origin', sector_id: 1, name: 'Origin' },
      { id: 'sec-dest', sector_id: 2, name: 'Dest' },
    ]);

    render(<WarpTunnelsManager />);

    expect(await screen.findByText('Test Tunnel')).toBeTruthy();
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
    expect(screen.queryByText('Holding')).toBeNull();
  });

  it('still lists tunnels when the sectors fetch fails (no invented holdings)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/warp-tunnels')) {
        return { data: { warp_tunnels: [sampleTunnel] } };
      }
      if (String(url).includes('/sectors')) {
        throw axiosError(500);
      }
      return { data: {} };
    });

    render(<WarpTunnelsManager />);

    expect(await screen.findByText('Test Tunnel')).toBeTruthy();
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
  });
});
