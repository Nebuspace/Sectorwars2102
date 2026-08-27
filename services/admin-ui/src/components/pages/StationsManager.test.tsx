import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import StationsManager from './StationsManager';
import { api } from '../../utils/auth';

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn(async () => false);

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('StationsManager scope errors (LEG-966)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.universe.stations'),
    );

    render(<StationsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.universe\.stations/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<StationsManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('StationsManager Soft-ORDER Add Port station_class (LEG-1461)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/ports')) {
        return { data: { ports: [], total: 0 } };
      }
      if (String(url).includes('/sectors')) {
        return {
          data: {
            sectors: [{ id: 'sec-uuid', sector_id: 42, name: 'Alpha', has_port: false }],
          },
        };
      }
      if (String(url).includes('/players')) {
        return { data: { players: [] } };
      }
      return { data: {} };
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { station_id: 'new', station_name: 'Test Port', sector_id: 42 },
    });
  });

  it('POSTs station_class CLASS_N without demoted create fields', async () => {
    render(<StationsManager />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Add New Station/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add New Station/i }));

    await waitFor(() => {
      expect(screen.getByText(/Add New Port/i)).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText(/Enter port name/i), {
      target: { value: 'New Port' },
    });
    fireEvent.change(screen.getByDisplayValue(/Select a sector/i), {
      target: { value: '42' },
    });
    const classSelect = screen.getByDisplayValue(/CLASS_1 - Mining/i);
    fireEvent.change(classSelect, { target: { value: 'CLASS_3' } });

    fireEvent.click(screen.getByRole('button', { name: /^Create Port$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    const postCall = vi.mocked(api.post).mock.calls.find((c) =>
      String(c[0]).includes('/admin/ports'),
    );
    expect(postCall).toBeTruthy();
    const payload = postCall![1] as Record<string, unknown>;
    expect(payload).toEqual(
      expect.objectContaining({
        name: 'New Port',
        sector_id: '42',
        station_class: 'CLASS_3',
      }),
    );
    expect(payload).not.toHaveProperty('station_type');
    expect(payload).not.toHaveProperty('max_capacity');
    expect(payload).not.toHaveProperty('security_level');
    expect(payload).not.toHaveProperty('docking_fee');
  });
});

describe('StationsManager update-stock-levels (LEG-1712)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.get).mockResolvedValue({ data: { stations: [], total: 0 } });
  });

  it('exposes update stock levels control', async () => {
    render(<StationsManager />);
    expect(await screen.findByLabelText('Update port stock levels')).toBeTruthy();
  });

  it('posts tip update-stock-levels path and toasts ports_updated count', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ports_updated: 4 } });
    render(<StationsManager />);

    fireEvent.click(await screen.findByLabelText('Update port stock levels'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/ports/update-stock-levels');
    });
    expect(toastSuccess).toHaveBeenCalledWith('Updated stock levels for 4 port(s)');
  });

  it('stock-levels 403 surfaces formatAdminApiError ECONOMY_INTERVENE scope copy', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    render(<StationsManager />);

    fireEvent.click(await screen.findByLabelText('Update port stock levels'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/ECONOMY_INTERVENE|Access denied/i);
  });

  it('stock-levels 429 surfaces admin rate-limit helper copy', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 429, data: {} },
    });
    render(<StationsManager />);

    fireEvent.click(await screen.findByLabelText('Update port stock levels'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('skips stock-levels POST when operator cancels confirm', async () => {
    confirmMock.mockResolvedValue(false);
    render(<StationsManager />);

    fireEvent.click(await screen.findByLabelText('Update port stock levels'));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe('StationsManager AddPortModal load honesty (LEG-2399)', () => {
  const stationsOk = { data: { stations: [], total: 0 } };
  const sectorsOk = {
    data: {
      sectors: [{ id: 'sec-uuid', sector_id: 42, name: 'Alpha', has_port: false }],
    },
  };
  const playersOk = { data: { players: [] } };

  const mockGets = (overrides: { sectors?: unknown; players?: unknown }) => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/stations')) {
        return stationsOk;
      }
      if (String(url).includes('/sectors')) {
        if (overrides.sectors) {
          throw overrides.sectors;
        }
        return sectorsOk;
      }
      if (String(url).includes('/players')) {
        if (overrides.players) {
          throw overrides.players;
        }
        return playersOk;
      }
      return { data: {} };
    });
  };

  const openAddPortModal = async () => {
    render(<StationsManager />);
    fireEvent.click(await screen.findByRole('button', { name: /Add New Station/i }));
    expect(await screen.findByText(/Add New Port/i)).toBeTruthy();
  };

  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('sectors 403 surfaces formatAdminApiError station-management scope copy', async () => {
    mockGets({ sectors: axiosError(403) });
    await openAddPortModal();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const messages = toastError.mock.calls.map((c) => String(c[0]));
    expect(messages.some((m) => /Access denied|station management/i.test(m))).toBe(true);
    expect(messages).not.toContain('Failed to load sectors. Please try again.');
  });

  it('sectors 429 surfaces admin rate-limit helper copy', async () => {
    mockGets({ sectors: axiosError(429) });
    await openAddPortModal();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
    expect(toastError.mock.calls.map((c) => String(c[0]))).not.toContain(
      'Failed to load sectors. Please try again.',
    );
  });

  it('players 403 surfaces formatAdminApiError station-management scope copy', async () => {
    mockGets({ players: axiosError(403) });
    await openAddPortModal();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const messages = toastError.mock.calls.map((c) => String(c[0]));
    expect(messages.some((m) => /Access denied|station management/i.test(m))).toBe(true);
    expect(messages).not.toContain('Failed to load players. Please try again.');
  });

  it('players 429 surfaces admin rate-limit helper copy', async () => {
    mockGets({ players: axiosError(429) });
    await openAddPortModal();

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
    expect(toastError.mock.calls.map((c) => String(c[0]))).not.toContain(
      'Failed to load players. Please try again.',
    );
  });
});
