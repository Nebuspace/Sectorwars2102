import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import StationsManager from './StationsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => vi.fn().mockResolvedValue(false),
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
