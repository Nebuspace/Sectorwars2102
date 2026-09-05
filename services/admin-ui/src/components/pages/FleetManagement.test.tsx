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

describe('FleetManagement ship registry backfill (LEG-1682)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('exposes backfill registry control', async () => {
    mockFleetGets();
    render(<FleetManagement />);

    expect(await screen.findByLabelText('Backfill ship registry')).toBeTruthy();
  });

  it('posts tip registry/backfill path and toasts backfilled count', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockResolvedValue({ data: { backfilled: 3 } });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Backfill ship registry'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/ships/registry/backfill');
    });
    expect(toastSuccess).toHaveBeenCalledWith('Backfilled 3 ship registry row(s)');
  });

  it('backfill 403 surfaces formatAdminApiError SHIPS_MANAGE scope copy', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Backfill ship registry'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/SHIPS_MANAGE|Access denied/i);
  });

  it('backfill 429 surfaces admin rate-limit helper copy', async () => {
    mockFleetGets();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Backfill ship registry'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('skips backfill POST when operator cancels confirm', async () => {
    mockFleetGets();
    confirmMock.mockResolvedValue(false);
    render(<FleetManagement />);

    fireEvent.click(await screen.findByLabelText('Backfill ship registry'));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(api.post).not.toHaveBeenCalled();
  });
});

function modalForm(title: string | RegExp): HTMLFormElement {
  const heading = screen.getByRole('heading', { name: title });
  const form = heading.closest('.modal')?.querySelector('form');
  if (!form) {
    throw new Error('modal form not found');
  }
  return form;
}

describe('FleetManagement ship CRUD+teleport formatAdminApiError (LEG-2395)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  async function readyFleet() {
    mockFleetGets();
    render(<FleetManagement />);
    await screen.findByText('Nebula Runner');
  }

  it('create 403 surfaces formatAdminApiError fleet-manage scope copy', async () => {
    await readyFleet();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    fireEvent.click(screen.getByText('+ Create Ship'));
    const form = modalForm('Create New Ship');
    fireEvent.change(form.querySelector('input[type="text"]') as HTMLInputElement, {
      target: { value: 'New Hull' },
    });
    fireEvent.change(form.querySelectorAll('select')[1] as HTMLSelectElement, {
      target: { value: 'p1' },
    });
    fireEvent.submit(form);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const message = String(toastError.mock.calls[0][0]);
    expect(message).toMatch(/admin\.ships\.manage|Access denied/i);
    expect(message).not.toBe('Failed to create ship');
  });

  it('create 429 surfaces admin rate-limit helper copy', async () => {
    await readyFleet();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    fireEvent.click(screen.getByText('+ Create Ship'));
    fireEvent.submit(modalForm('Create New Ship'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('update 403 surfaces formatAdminApiError fleet-manage scope copy', async () => {
    await readyFleet();
    vi.mocked(api.put).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    fireEvent.click(screen.getByLabelText('Edit Ship'));
    fireEvent.submit(modalForm(/Edit Ship/));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const message = String(toastError.mock.calls[0][0]);
    expect(message).toMatch(/admin\.ships\.manage|Access denied/i);
    expect(message).not.toBe('Failed to update ship');
  });

  it('update 429 surfaces admin rate-limit helper copy', async () => {
    await readyFleet();
    vi.mocked(api.put).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    fireEvent.click(screen.getByLabelText('Edit Ship'));
    fireEvent.submit(modalForm(/Edit Ship/));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('surfaces honest fallback on update PUT TypeError/network collapse (LEG-2973)', async () => {
    await readyFleet();
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));
    fireEvent.click(screen.getByLabelText('Edit Ship'));
    fireEvent.submit(modalForm(/Edit Ship/));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/ships/${sampleShip.id}`,
        expect.anything(),
      );
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update ship/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('delete 403 surfaces formatAdminApiError fleet-manage scope copy', async () => {
    await readyFleet();
    vi.mocked(api.delete).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    fireEvent.click(screen.getByLabelText('Delete Ship'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const message = String(toastError.mock.calls[0][0]);
    expect(message).toMatch(/admin\.ships\.manage|Access denied/i);
    expect(message).not.toBe('Failed to delete ship');
  });

  it('delete 429 surfaces admin rate-limit helper copy', async () => {
    await readyFleet();
    vi.mocked(api.delete).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    fireEvent.click(screen.getByLabelText('Delete Ship'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('teleport 403 surfaces formatAdminApiError fleet-manage scope copy', async () => {
    await readyFleet();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    fireEvent.click(screen.getByLabelText('Teleport Ship'));
    fireEvent.submit(modalForm(/Teleport Ship/));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const message = String(toastError.mock.calls[0][0]);
    expect(message).toMatch(/admin\.ships\.manage|Access denied/i);
    expect(message).not.toBe('Failed to teleport ship');
  });

  it('teleport 429 surfaces admin rate-limit helper copy', async () => {
    await readyFleet();
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    fireEvent.click(screen.getByLabelText('Teleport Ship'));
    fireEvent.submit(modalForm(/Teleport Ship/));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });
});

describe('FleetManagement TypeError densify (LEG-3067)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it('surfaces honest fallback on fleet load network collapse', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FleetManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch fleet data/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to fetch fleet data/i).textContent ?? '';
    expect(text).toMatch(/Failed to fetch fleet data/i);
    expect(text).not.toMatch(/TypeError/i);
    // Raw TypeError.message alone — not the invent=0 fallback phrase
    expect(text).not.toBe('Failed to fetch');
  });
});

describe('FleetManagement axios Network Error densify (LEG-3400)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on load to honest fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<FleetManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch fleet data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch fleet data/i).textContent ?? '';
    expect(text).toMatch(/Failed to fetch fleet data/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios-shaped Network Error on update PUT to honest toast', async () => {
    mockFleetGets();
    render(<FleetManagement />);
    await screen.findByText('Nebula Runner');
    vi.mocked(api.put).mockRejectedValue(new Error('Network Error'));

    fireEvent.click(screen.getByLabelText('Edit Ship'));
    fireEvent.submit(modalForm(/Edit Ship/));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/ships/${sampleShip.id}`,
        expect.anything(),
      );
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update ship/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });
});
