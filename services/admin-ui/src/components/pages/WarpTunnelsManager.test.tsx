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
  vi.mocked(api.get).mockResolvedValue({ data: { warp_tunnels: [sampleTunnel] } });
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
