import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import FleetManagement from './FleetManagement';
import { api } from '../../utils/auth';

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn(async () => true);

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useFleetUpdates: () => undefined,
}));

vi.mock('../charts/FleetHealthReport', () => ({
  default: () => <div data-testid="fleet-health-stub" />,
}));

vi.mock('../fleet/FleetOperationsTab', () => ({
  default: () => <div data-testid="fleet-ops-stub" />,
}));

const sampleShip = {
  id: 'ship-1',
  name: 'Nebula Runner',
  ship_type: 'LIGHT_FREIGHTER',
  owner_id: 'p1',
  owner_name: 'Ace',
  current_sector_id: 42,
  maintenance_rating: 55,
  cargo_used: 10,
  cargo_capacity: 100,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

function mockFleetGets() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/admin/ships/comprehensive')) {
      return {
        data: {
          ships: [sampleShip],
          total_count: 1,
          total_pages: 1,
        },
      };
    }
    if (url.includes('/admin/players/comprehensive')) {
      return { data: { players: [{ id: 'p1', username: 'Ace' }] } };
    }
    return { data: {} };
  });
}

describe('FleetManagement emergency repair/refuel (LEG-1651)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('loads ships and exposes emergency repair + refuel controls', async () => {
    mockFleetGets();
    render(<FleetManagement />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/admin/ships/comprehensive')
      );
    });

    expect(await screen.findByText('Nebula Runner')).toBeTruthy();
    expect(screen.getByLabelText('Emergency repair Nebula Runner')).toBeTruthy();
    expect(screen.getByLabelText('Emergency refuel Nebula Runner')).toBeTruthy();
  });

  it('repair posts tip emergency path with action repair and toasts message', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, message: 'Ship Nebula Runner fully repaired' },
    });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Emergency repair Nebula Runner'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/ships/ship-1/emergency', {
        action: 'repair',
      });
    });
    expect(toastSuccess).toHaveBeenCalledWith('Ship Nebula Runner fully repaired');
  });

  it('refuel posts tip emergency path with action refuel and toasts message', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, message: 'Ship Nebula Runner refueled' },
    });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Emergency refuel Nebula Runner'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/ships/ship-1/emergency', {
        action: 'refuel',
      });
    });
    expect(toastSuccess).toHaveBeenCalledWith('Ship Nebula Runner refueled');
  });

  it('emergency 403 surfaces formatAdminApiError SHIPS_MANAGE scope copy', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Emergency repair Nebula Runner'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/SHIPS_MANAGE|Access denied/i);
  });

  it('emergency 429 surfaces admin rate-limit helper copy', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Emergency refuel Nebula Runner'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('skips POST when operator cancels confirm', async () => {
    mockFleetGets();
    confirmMock.mockResolvedValue(false);
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Emergency repair Nebula Runner'));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(api.post).not.toHaveBeenCalled();
  });
});
